from unittest.mock import MagicMock, patch

import tui


def _sync(working, on_battery):
    caf = tui.CaffeinateController()
    with patch.object(tui.CaffeinateController, "_on_battery", return_value=on_battery), \
         patch.object(tui.subprocess, "Popen", return_value=MagicMock(poll=lambda: None)) as popen:
        caf.sync(working)
    return popen.call_count


def test_auto_holds_caffeinate_only_while_an_agent_works_on_ac():
    assert _sync(working=0, on_battery=False) == 0
    assert _sync(working=1, on_battery=False) == 1


def test_auto_never_holds_caffeinate_on_battery():
    assert _sync(working=3, on_battery=True) == 0
