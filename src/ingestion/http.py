"""Polite HTTP client: clear user agent, robots.txt, delay, retries with backoff.

Section 3.4.3 / 3.5.2: 60 s timeout, at least 5 s between requests, three
retries waiting 10, 20 and 40 s. After the last failure the caller puts the
item on the manual queue instead of failing the whole run.
"""

from __future__ import annotations

import logging
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests

log = logging.getLogger(__name__)


class DownloadError(RuntimeError):
    """Raised when a URL still fails after every retry."""


@dataclass
class PoliteSession:
    user_agent: str
    delay_s: float = 5.0
    timeout_s: float = 60.0
    max_retries: int = 3
    backoff_base_s: float = 10.0
    respect_robots: bool = True
    sleep: callable = time.sleep  # injectable for tests
    _session: requests.Session = field(default_factory=requests.Session, init=False, repr=False)
    _robots: dict[str, urllib.robotparser.RobotFileParser | None] = field(default_factory=dict, init=False)
    _last_request: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self._session.headers["User-Agent"] = self.user_agent

    # -- robots.txt -------------------------------------------------------------
    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlparse(url)
        root = f"{parts.scheme}://{parts.netloc}"
        if root not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            try:
                resp = self._session.get(urljoin(root, "/robots.txt"), timeout=self.timeout_s)
                if resp.status_code >= 400:
                    self._robots[root] = None  # no robots.txt: everything allowed
                else:
                    parser.parse(resp.text.splitlines())
                    self._robots[root] = parser
            except requests.RequestException as exc:
                log.warning("could not read robots.txt for %s: %s", root, exc)
                self._robots[root] = None
        parser = self._robots[root]
        return True if parser is None else parser.can_fetch(self.user_agent, url)

    # -- requests ---------------------------------------------------------------
    def _wait_turn(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if self._last_request and elapsed < self.delay_s:
            self.sleep(self.delay_s - elapsed)

    def get(self, url: str) -> requests.Response:
        """GET with delay, retries and exponential backoff."""
        if not self.allowed(url):
            raise DownloadError(f"robots.txt disallows {url}")
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._wait_turn()
            try:
                self._last_request = time.monotonic()
                resp = self._session.get(url, timeout=self.timeout_s)
                if resp.status_code == 200:
                    return resp
                last_error = DownloadError(f"HTTP {resp.status_code} for {url}")
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    break  # a client error will not fix itself
            except requests.RequestException as exc:
                last_error = exc
            if attempt < self.max_retries:
                wait = self.backoff_base_s * (2 ** attempt)
                log.warning("retry %d/%d for %s in %.0fs (%s)", attempt + 1, self.max_retries, url, wait, last_error)
                self.sleep(wait)
        raise DownloadError(str(last_error))
