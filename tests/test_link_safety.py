"""Source links are rendered as hrefs, so only http(s) URLs are accepted, and the
resolver is chosen by exact host — not by substring."""

from __future__ import annotations

import pytest

from tests.conftest import CREATOR_HEADERS


@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "ftp://zkillboard.com/related/30002222/202606101500/",
    "zkillboard.com/related/30002222/202606101500/",
])
def test_non_http_link_sources_are_rejected(make_client, tmp_path, url: str) -> None:  # type: ignore[no-untyped-def]
    client = make_client(DB_PATH=str(tmp_path / "t.db"))
    for body in ({"url": url}, {"sources": [{"kind": "link", "url": url}]}):
        r = client.post("/api/brs", json=body, headers=CREATOR_HEADERS)
        assert r.status_code == 400, (body, r.text)


def test_resolver_is_chosen_by_exact_host() -> None:
    from app.config import Settings
    from app.ingest.sources.aurora import AuroraSource
    from app.ingest.sources.factory import get_source
    from app.ingest.sources.zkillboard import ZkbSource

    s = Settings(data_source="real")
    assert isinstance(get_source("https://zkillboard.com/related/1/202606101500/", s), ZkbSource)
    assert isinstance(get_source("https://www.zkillboard.com/related/1/202606101500/", s), ZkbSource)
    assert isinstance(get_source("https://br.evetools.org/br/abc", s), AuroraSource)
    for evil in ("https://zkillboard.com.evil.example/related/1/202606101500/",
                 "https://notzkillboard.com/related/1/202606101500/",
                 "https://evil.example/?x=evetools.org"):
        with pytest.raises(ValueError):
            get_source(evil, s)
