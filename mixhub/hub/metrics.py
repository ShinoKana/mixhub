"""System-wide CPU / memory metrics matching Activity Monitor (Mach / sysctl fast path, `vm_stat` fallback)."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time

LINUX_MEMINFO = "/proc/meminfo"


def _linux_memory(path: str) -> tuple[float, float, int] | None:
    """`(total_mb, used_mb, pressure_level)` from `/proc/meminfo`: used = MemTotal - MemAvailable; the pressure level
    maps the available share onto macOS's scale (`1` normal, `2` under 20 % available, `4` under 10 %)."""
    try:
        fields = {}
        with open(path, encoding="ascii", errors="replace") as f:
            for line in f:
                key, _, rest = line.partition(":")
                parts = rest.split()
                if parts and parts[0].isdigit():
                    fields[key] = int(parts[0])  # kB
        total, available = fields["MemTotal"], fields["MemAvailable"]
    except (OSError, KeyError):
        return None
    if total <= 0:
        return None
    share = available / total
    pressure = 4 if share < 0.10 else 2 if share < 0.20 else 1
    return round(total / 1024.0, 1), round(max(0, total - available) / 1024.0, 1), pressure


class SystemMetricsMixin:
    """System resource metrics with a 5 s class-level cache."""

    _SYS_METRICS_CACHE: dict = {"ts": 0.0, "data": None, "cpu_ticks": None}

    @staticmethod
    def _darwin_fast_system_metrics() -> dict | None:
        """Zero-subprocess macOS Mach/sysctl collector (`host_statistics64` + `HOST_CPU_LOAD_INFO` + `sysctlbyname`, ~0.04ms)."""
        if sys.platform != "darwin":
            return None
        try:
            import ctypes
            import ctypes.util

            lib = getattr(SystemMetricsMixin, "_DARWIN_LIBSYSTEM", None)
            if lib is None:
                lib_path = ctypes.util.find_library("System") or "/usr/lib/libSystem.B.dylib"
                lib = ctypes.CDLL(lib_path)
                SystemMetricsMixin._DARWIN_LIBSYSTEM = lib

            def _sysctl_u64(name: bytes) -> int:
                val = ctypes.c_uint64(0)
                sz = ctypes.c_size_t(ctypes.sizeof(val))
                return int(val.value) if lib.sysctlbyname(name, ctypes.byref(val), ctypes.byref(sz), None, 0) == 0 else 0

            def _sysctl_u32(name: bytes) -> int:
                val = ctypes.c_uint32(0)
                sz = ctypes.c_size_t(ctypes.sizeof(val))
                return int(val.value) if lib.sysctlbyname(name, ctypes.byref(val), ctypes.byref(sz), None, 0) == 0 else 0

            class _VmStat64(ctypes.Structure):
                _fields_ = [
                    ("free_count", ctypes.c_uint32),
                    ("active_count", ctypes.c_uint32),
                    ("inactive_count", ctypes.c_uint32),
                    ("wire_count", ctypes.c_uint32),
                    ("zero_fill_count", ctypes.c_uint64),
                    ("reactivations", ctypes.c_uint64),
                    ("pageins", ctypes.c_uint64),
                    ("pageouts", ctypes.c_uint64),
                    ("faults", ctypes.c_uint64),
                    ("cow_faults", ctypes.c_uint64),
                    ("lookups", ctypes.c_uint64),
                    ("hits", ctypes.c_uint64),
                    ("purges", ctypes.c_uint64),
                    ("purgeable_count", ctypes.c_uint32),
                    ("speculative_count", ctypes.c_uint32),
                    ("decompressions", ctypes.c_uint64),
                    ("compressions", ctypes.c_uint64),
                    ("swapins", ctypes.c_uint64),
                    ("swapouts", ctypes.c_uint64),
                    ("compressor_page_count", ctypes.c_uint32),
                    ("throttled_count", ctypes.c_uint32),
                    ("external_page_count", ctypes.c_uint32),
                    ("internal_page_count", ctypes.c_uint32),
                    ("total_uncompressed_pages_in_compressor", ctypes.c_uint64),
                ]

            class _CpuLoadInfo(ctypes.Structure):
                _fields_ = [("cpu_ticks", ctypes.c_uint32 * 4)]

            tot_bytes = _sysctl_u64(b"hw.memsize")
            if tot_bytes <= 0:
                return None
            mem_total_mb = round(tot_bytes / (1024.0 * 1024.0), 1)
            pressure_level = _sysctl_u32(b"kern.memorystatus_vm_pressure_level") or 1
            page_size = _sysctl_u32(b"hw.pagesize") or 16384

            host_port = lib.mach_host_self()
            vm = _VmStat64()
            vm_cnt = ctypes.c_uint32(ctypes.sizeof(vm) // ctypes.sizeof(ctypes.c_int32))
            if lib.host_statistics64(host_port, 4, ctypes.byref(vm), ctypes.byref(vm_cnt)) != 0:
                return None
            free_pages = int(vm.free_count) + int(vm.speculative_count)
            cached_pages = int(vm.external_page_count) + int(vm.purgeable_count)
            used_bytes = max(0, tot_bytes - (free_pages + cached_pages) * page_size)
            mem_used_mb = round(used_bytes / (1024.0 * 1024.0), 1)
            mem_pct = min(100, max(0, int(round((mem_used_mb * 100.0) / max(1.0, mem_total_mb))))) if mem_used_mb > 0 else 0

            cpu_pct = 0
            cpu_info = _CpuLoadInfo()
            cpu_cnt = ctypes.c_uint32(4)
            if lib.host_statistics(host_port, 3, ctypes.byref(cpu_info), ctypes.byref(cpu_cnt)) == 0:
                cur_ticks = tuple(int(x) for x in cpu_info.cpu_ticks)
                prev_ticks = SystemMetricsMixin._SYS_METRICS_CACHE.get("cpu_ticks")
                SystemMetricsMixin._SYS_METRICS_CACHE["cpu_ticks"] = cur_ticks
                if prev_ticks is not None:
                    du = (cur_ticks[0] - prev_ticks[0]) + (cur_ticks[1] - prev_ticks[1]) + (cur_ticks[3] - prev_ticks[3])
                    dt = du + (cur_ticks[2] - prev_ticks[2])
                    if dt > 0:
                        cpu_pct = min(100, max(0, int(round((du * 100.0) / dt))))
                else:
                    ncpu = max(1, os.cpu_count() or 8)
                    load1 = os.getloadavg()[0]
                    cpu_pct = min(100, max(0, int(round((load1 / ncpu) * 100.0))))
            return {
                "sysCpuPercent": cpu_pct,
                "sysMemoryUsedMB": mem_used_mb,
                "sysMemoryTotalMB": mem_total_mb,
                "sysMemoryPercent": mem_pct,
                "sysMemoryPressureLevel": pressure_level,
            }
        except Exception:
            return None

    @staticmethod
    def get_system_resource_metrics(now_mono: float | None = None, force: bool = False) -> dict:
        """
        Collect system-wide CPU % (`0~100%`) and Memory Used / Pressure aligned 1:1 with macOS Activity Monitor:
          - `sysCpuPercent`: system-wide CPU utilization clamped to `0..100`.
          - `sysMemoryUsedMB`: `Total Physical RAM - (Pages free + File-backed pages + Pages purgeable) * page_size`
            from Mach `host_statistics64` / `vm_stat` + `sysctl hw.memsize` (matches Activity Monitor's exact `Memory Used`).
          - `sysMemoryTotalMB`: total physical RAM in MB (`sysctl -n hw.memsize`).
          - `sysMemoryPercent`: `int(round(sysMemoryUsedMB * 100.0 / sysMemoryTotalMB))`.
          - `sysMemoryPressureLevel`: `int(sysctl -n kern.memorystatus_vm_pressure_level)` (`1`=normal, `2`=warn, `4`=critical).
        Cached for `5.0s` so repeated calls cost 0.000ms.
        """
        now = time.monotonic() if now_mono is None else float(now_mono)
        cached = SystemMetricsMixin._SYS_METRICS_CACHE.get("data")
        if not force and cached is not None and (now - float(SystemMetricsMixin._SYS_METRICS_CACHE.get("ts", 0.0))) < 5.0:
            return dict(cached)

        if sys.platform == "darwin" and type(subprocess.check_output).__name__ == "function":
            fast_res = SystemMetricsMixin._darwin_fast_system_metrics()
            if fast_res is not None:
                SystemMetricsMixin._SYS_METRICS_CACHE["ts"] = now
                SystemMetricsMixin._SYS_METRICS_CACHE["data"] = fast_res
                return dict(fast_res)

        ncpu = max(1, os.cpu_count() or 8)
        cpu_pct = 0
        try:
            ps_out = subprocess.check_output(["ps", "-A", "-o", "%cpu"], text=True, stderr=subprocess.DEVNULL, timeout=1.2)
            total_cpu = sum(float(ln.strip() or 0.0) for ln in ps_out.splitlines()[1:] if ln.strip().replace(".", "", 1).isdigit())
            cpu_pct = min(100, max(0, int(round(total_cpu / ncpu))))
        except Exception:
            try:
                load1 = os.getloadavg()[0]
                cpu_pct = min(100, max(0, int(round((load1 / ncpu) * 100.0))))
            except Exception:
                cpu_pct = 0

        mem_used_mb = 0.0
        mem_total_mb = 16384.0
        pressure_level = 1
        if sys.platform == "darwin":
            try:
                tot_raw = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True, stderr=subprocess.DEVNULL, timeout=0.8).strip()
                tot_bytes = int(tot_raw)
                mem_total_mb = round(tot_bytes / (1024.0 * 1024.0), 1)
            except Exception:
                tot_bytes = int(mem_total_mb * 1024 * 1024)
            try:
                pres_raw = subprocess.check_output(
                    ["sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=0.8,
                ).strip()
                if pres_raw.isdigit():
                    pressure_level = int(pres_raw)
            except Exception:
                pressure_level = 1
            try:
                vm_out = subprocess.check_output(["vm_stat"], text=True, stderr=subprocess.DEVNULL, timeout=0.8)
                ps_m = re.search(r"page size of (\d+) bytes", vm_out)
                page_size = int(ps_m.group(1)) if ps_m else 16384
                pages: dict[str, int] = {}
                for line in vm_out.splitlines()[1:]:
                    if ":" not in line:
                        continue
                    k, v = line.split(":", 1)
                    v_clean = v.strip().rstrip(".")
                    if v_clean.isdigit():
                        pages[k.strip().strip('"')] = int(v_clean)
                free_pages = pages.get("Pages free", 0)
                cached_pages = pages.get("File-backed pages", 0) + pages.get("Pages purgeable", 0)
                if tot_bytes > 0 and "File-backed pages" in pages:
                    used_bytes = max(0, tot_bytes - (free_pages + cached_pages) * page_size)
                else:
                    used_pages = (
                        pages.get("Pages active", 0)
                        + pages.get("Pages wired down", 0)
                        + pages.get("Pages occupied by compressor", 0)
                        - pages.get("Pages purgeable", 0)
                    )
                    used_bytes = max(0, used_pages * page_size)
                mem_used_mb = round(used_bytes / (1024.0 * 1024.0), 1)
            except Exception:
                mem_used_mb = 0.0
        elif sys.platform.startswith("linux"):
            linux_mem = _linux_memory(LINUX_MEMINFO)
            if linux_mem is not None:
                mem_total_mb, mem_used_mb, pressure_level = linux_mem

        mem_pct = min(100, max(0, int(round((mem_used_mb * 100.0) / max(1.0, mem_total_mb))))) if mem_used_mb > 0 else 0
        res = {
            "sysCpuPercent": cpu_pct,
            "sysMemoryUsedMB": mem_used_mb,
            "sysMemoryTotalMB": mem_total_mb,
            "sysMemoryPercent": mem_pct,
            "sysMemoryPressureLevel": pressure_level,
        }
        SystemMetricsMixin._SYS_METRICS_CACHE["ts"] = now
        SystemMetricsMixin._SYS_METRICS_CACHE["data"] = res
        return dict(res)

    system_metrics = staticmethod(get_system_resource_metrics)
