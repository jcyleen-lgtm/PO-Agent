"""Frontend wiring: the page references only assets that exist and are served; CSS/JS live in static/."""

import os
import re

from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _page():
    with open(os.path.join(ROOT, "templates", "dashboard.html"), encoding="utf-8") as fh:
        return fh.read()


def test_page_has_no_inline_stylesheet_or_app_script():
    html = _page()
    assert "<style" not in html
    # only the tiny theme bootstrap (must run before first paint) stays inline
    inline = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert len(inline) == 1 and "data-theme" in inline[0]


def test_every_static_asset_exists_and_is_served():
    import app as A
    c = TestClient(A.app)
    refs = re.findall(r'(?:href|src)="(/static/[^"]+)"', _page())
    assert refs, "no static assets referenced"
    for ref in refs:
        assert os.path.exists(os.path.join(ROOT, ref.lstrip("/"))), ref
        r = c.get(ref)
        assert r.status_code == 200, ref
