"""`mixhub.tui.hubtui.evq`: `select.kqueue` where the platform has it (macOS), else `PollQueue`, a stand-in with the
same `control()` shape for the subset the wait loops use: readable fds, terminal resize, and changed files."""
import os
import select
import signal
import time

KQ_EV_ADD = getattr(select, "KQ_EV_ADD", 0x1)
KQ_EV_CLEAR = getattr(select, "KQ_EV_CLEAR", 0x20)
KQ_FILTER_READ = getattr(select, "KQ_FILTER_READ", -1)
KQ_FILTER_VNODE = getattr(select, "KQ_FILTER_VNODE", -4)
KQ_FILTER_SIGNAL = getattr(select, "KQ_FILTER_SIGNAL", -6)
KQ_NOTE_DELETE = getattr(select, "KQ_NOTE_DELETE", 0x1)
KQ_NOTE_WRITE = getattr(select, "KQ_NOTE_WRITE", 0x2)
KQ_NOTE_EXTEND = getattr(select, "KQ_NOTE_EXTEND", 0x4)
KQ_NOTE_ATTRIB = getattr(select, "KQ_NOTE_ATTRIB", 0x8)
KQ_NOTE_RENAME = getattr(select, "KQ_NOTE_RENAME", 0x20)

# Files and the terminal size are polled at this interval; readable fds wake the wait at once.
POLL_SLICE = 0.25


class _Event:
    def __init__(self, ident, filter=KQ_FILTER_READ, flags=KQ_EV_ADD, fflags=0):
        self.ident = ident
        self.filter = filter
        self.flags = flags
        self.fflags = fflags


def _file_sig(fd):
    try:
        st = os.fstat(fd)
    except OSError:
        return None
    return (st.st_ino, st.st_nlink, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _term_size():
    for fd in (1, 0):
        try:
            return tuple(os.get_terminal_size(fd))
        except OSError:
            continue
    return None


class PollQueue:
    def __init__(self):
        self._read = []
        self._files = {}
        self._size = None
        self._watch_size = False

    def control(self, changelist, max_events, timeout=None):
        for ev in changelist or ():
            if not ev.flags & KQ_EV_ADD:
                continue
            if ev.filter == KQ_FILTER_READ and ev.ident not in self._read:
                self._read.append(ev.ident)
            elif ev.filter == KQ_FILTER_VNODE and ev.ident not in self._files:
                self._files[ev.ident] = _file_sig(ev.ident)
            elif ev.filter == KQ_FILTER_SIGNAL and ev.ident == signal.SIGWINCH and not self._watch_size:
                self._watch_size, self._size = True, _term_size()
        if not max_events:
            return []
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        while True:
            left = None if deadline is None else max(0.0, deadline - time.monotonic())
            wait = POLL_SLICE if left is None else min(POLL_SLICE, left)
            ready = []
            try:
                ready = select.select(self._read, [], [], wait)[0] if self._read else []
            except (OSError, ValueError):
                pass
            if not self._read:
                time.sleep(wait)
            events = [_Event(fd, KQ_FILTER_READ) for fd in ready]
            if self._watch_size:
                size = _term_size()
                if size != self._size:
                    self._size = size
                    events.append(_Event(signal.SIGWINCH, KQ_FILTER_SIGNAL))
            for fd, sig in self._files.items():
                now = _file_sig(fd)
                if now != sig:
                    self._files[fd] = now
                    events.append(_Event(fd, KQ_FILTER_VNODE))
            if events or (deadline is not None and time.monotonic() >= deadline):
                return events[:max_events]

    def close(self):
        self._read, self._files = [], {}


if hasattr(select, "kqueue"):
    kqueue, kevent = select.kqueue, select.kevent
else:
    kqueue, kevent = PollQueue, _Event
