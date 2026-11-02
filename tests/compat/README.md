Compatibility modules for the test suite.

Before the code moved into the `mixhub` package, it lived in single files at the repository root (`server.py`,
`tui.py`, ...). Many tests still reach the code through those names (`server.Hub()`, `tui.HubTUI`, patching
`tui.<name>`), so these thin re-export modules keep them working. They are not part of MixHub itself: new code and
new tests import from `mixhub` directly. `tests/conftest.py` puts this directory on `sys.path`.
