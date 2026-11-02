"""A link to the web workbench from another site (a chat message, a notification) opens the page; the API stays
same-origin only."""
import unittest
from types import SimpleNamespace

from mixhub.web.handler import Handler


def handler(path, **headers):
    h = Handler.__new__(Handler)
    h.headers = {"Host": "127.0.0.1:8765", **headers}
    h.server = SimpleNamespace(server_port=8765)
    h.path = path
    return h


CROSS_SITE_NAV = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}


class WebNavigationTest(unittest.TestCase):
    def test_a_link_from_another_site_opens_the_page(self):
        self.assertTrue(handler("/", **CROSS_SITE_NAV).allowed())
        self.assertTrue(handler("/index.html", **CROSS_SITE_NAV).allowed())

    def test_the_api_is_still_closed_to_other_sites(self):
        self.assertFalse(handler("/api/sessions", **CROSS_SITE_NAV).allowed())
        self.assertFalse(handler("/api/sessions", **{"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors"}).allowed())
        self.assertFalse(handler("/", **{"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"}).allowed())

    def test_writes_and_wrong_hosts_stay_rejected(self):
        self.assertFalse(handler("/", **CROSS_SITE_NAV).allowed(write=True))
        self.assertFalse(handler("/", **{**CROSS_SITE_NAV, "Host": "evil.example:8765"}).allowed())


if __name__ == "__main__":
    unittest.main()
