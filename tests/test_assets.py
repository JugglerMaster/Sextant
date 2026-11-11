from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "runner_control" / "static"


def test_static_assets_do_not_require_inline_styles():
    """The CSP uses style-src 'self', so inline style attributes would be blocked."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'style="' not in html
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert ".style." not in js
