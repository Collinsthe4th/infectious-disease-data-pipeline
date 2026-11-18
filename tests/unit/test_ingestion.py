from datetime import date

import pytest
import requests

from src.common.diseases import DISEASES
from src.ingestion.hashing import sha256_bytes, sha256_file
from src.ingestion.http import DownloadError, PoliteSession
from src.ingestion.inbox import move_to_raw, scan_inbox
from src.ingestion.ncdc_crawler import NcdcCrawler, listing_url, parse_listing

LISTING_HTML = """
<html><body><h2>An update of Lassa fever outbreak in Nigeria</h2>
<table class="table">
 <tr><th>SN</th><th>Title</th><th></th></tr>
 <tr><td>1</td><td>An update of Lassa fever outbreak in Nigeria for Week 44</td>
     <td><a href="/themes/common/files/sitreps/e1cad9ad801ce69189f3fc9994919a5f.pdf">Download</a></td></tr>
 <tr><td>2</td><td>An update of Lassa fever outbreak in Nigeria Week 45 | 081126</td>
     <td><a href="https://ncdc.gov.ng/themes/common/files/sitreps/15c77724006c3aa49a953ebd09cdc438.pdf">Download</a></td></tr>
 <tr><td>3</td><td>Unrelated</td><td><a href="/about">About</a></td></tr>
</table></body></html>"""


def test_parse_listing_reads_title_week_and_upload_date():
    entries = parse_listing(LISTING_HTML, "https://ncdc.gov.ng/diseases/sitreps/?cat=5", "LASSA")
    assert len(entries) == 2
    assert entries[0].url == "https://ncdc.gov.ng/themes/common/files/sitreps/e1cad9ad801ce69189f3fc9994919a5f.pdf"
    assert entries[0].epi_week == 44 and entries[0].listing_date is None
    assert entries[1].epi_week == 45 and entries[1].listing_date == date(2026, 11, 8)


def test_listing_url_uses_category_id():
    assert "cat=5&name=An%20update%20of%20Lassa%20fever" in listing_url("https://ncdc.gov.ng", DISEASES["LASSA"])


class FakeResponse:
    def __init__(self, status=200, content=b"%PDF-1.4 test", text=""):
        self.status_code, self.content, self.text = status, content, text


def make_session(responses, robots=None):
    s = PoliteSession(user_agent="test", delay_s=0, max_retries=3, backoff_base_s=10, sleep=lambda _: None)
    calls = []

    def get(url, timeout):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return FakeResponse(404) if robots is None else FakeResponse(200, text=robots)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    s._session.get = get
    return s, calls


def test_retries_with_backoff_then_succeeds():
    waits = []
    s, calls = make_session([requests.ConnectionError("boom"), FakeResponse(500), FakeResponse(200)])
    s.sleep = waits.append
    assert s.get("https://x.test/a.pdf").status_code == 200
    assert waits == [10, 20]


def test_gives_up_after_three_retries():
    s, _ = make_session([FakeResponse(503)] * 4)
    with pytest.raises(DownloadError):
        s.get("https://x.test/a.pdf")


def test_robots_txt_is_respected():
    s, _ = make_session([], robots="User-agent: *\nDisallow: /private/")
    with pytest.raises(DownloadError):
        s.get("https://x.test/private/a.pdf")


def test_download_is_idempotent(tmp_path):
    s, _ = make_session([FakeResponse(200), FakeResponse(200)])
    crawler = NcdcCrawler(s, "https://x.test", tmp_path)
    entry = parse_listing(LISTING_HTML, "https://x.test/l", "LASSA")[0]
    stored: set[str] = set()
    first = crawler.download(entry, lambda h: h in stored)
    stored.add(first.sha256)
    assert first.sha256 == sha256_bytes(b"%PDF-1.4 test") and first.path.exists()
    assert crawler.download(entry, lambda h: h in stored) is None


def test_non_pdf_download_rejected(tmp_path):
    s, _ = make_session([FakeResponse(200, content=b"<html>error</html>")])
    entry = parse_listing(LISTING_HTML, "https://x.test/l", "LASSA")[0]
    with pytest.raises(DownloadError):
        NcdcCrawler(s, "https://x.test", tmp_path).download(entry, lambda h: False)


def test_inbox_scan_and_move(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "SYNTHETIC_a.csv").write_text("x")
    (inbox / "report.pdf").write_bytes(b"%PDF-1.4")
    (inbox / "notes.txt").write_text("ignored")
    items = scan_inbox(inbox)
    assert [i.source_path.name for i in items] == ["SYNTHETIC_a.csv", "report.pdf"]
    assert items[0].is_synthetic and not items[1].is_synthetic
    target = move_to_raw(items[1], tmp_path / "raw")
    assert target.exists() and not (inbox / "report.pdf").exists()
    assert sha256_file(target) == items[1].sha256
