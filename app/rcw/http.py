"""A polite HTTP client (brief: normal user agent, low volume, honour robots.txt, stop if blocked).

Every request is checked against the site's robots.txt (cached for a day) and spaced at least `delay` seconds
from the last one. 403 / 429 raise Blocked, which makes the poller back off that source for a day.
"""
import logging
import time
import urllib.robotparser
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
ROBOTS_TTL = 24 * 3600


class Blocked(Exception):
    """The site refused us (403 / 429): stop and back off."""


class Disallowed(Exception):
    """robots.txt does not allow this URL."""


def robots_parser(lines: list[str]) -> urllib.robotparser.RobotFileParser:
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(lines)
    return rp


class Polite:
    def __init__(self, delay: float = 2.0, timeout: float = 30.0):
        self.delay = delay
        self.client = httpx.Client(timeout=timeout, follow_redirects=True, headers={
            "User-Agent": USER_AGENT, "Accept-Language": "en-AU,en;q=0.9"})
        self._last = 0.0
        self._robots: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]] = {}
        self.requests = 0

    def _wait(self):
        gap = self._last + self.delay - time.monotonic()
        if gap > 0:
            time.sleep(gap)

    def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        fetched, rp = self._robots.get(host, (0.0, None))
        if time.time() - fetched > ROBOTS_TTL:
            self._wait()
            try:
                r = self.client.get(host + "/robots.txt")
                self.requests += 1
                if r.status_code in (401, 403):
                    rp = robots_parser(["User-agent: *", "Disallow: /"])
                elif r.status_code >= 400:
                    rp = None  # no robots.txt: everything allowed
                else:
                    rp = robots_parser(r.text.splitlines())
            except httpx.HTTPError as e:
                log.warning("robots.txt for %s unavailable (%s) - keeping the previous answer", host, e)
            finally:
                self._last = time.monotonic()
            self._robots[host] = (time.time(), rp)
        return rp is None or rp.can_fetch(USER_AGENT, url)

    def get(self, url: str, **kw) -> httpx.Response:
        if not self._allowed(url):
            raise Disallowed(f"robots.txt does not allow {url}")
        self._wait()
        try:
            r = self.client.get(url, **kw)
        finally:
            self._last = time.monotonic()
            self.requests += 1
        if r.status_code in (403, 429):
            raise Blocked(f"{r.status_code} from {urlsplit(url).netloc} - the site is refusing or rate-limiting us")
        r.raise_for_status()
        return r

    def close(self):
        self.client.close()
