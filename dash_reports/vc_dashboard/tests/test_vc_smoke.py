from data_importer.vc.canonicalize import (
    canonicalize_deals,
    deal_size_band,
)
from data_importer.vc.news.rss_adapter import message_hash
from dash_reports.vc_dashboard.pages import formatting


def test_deal_size_band():
    assert deal_size_band(None) == "Undisclosed"
    assert deal_size_band(5e6) == "<$10M"
    assert deal_size_band(75e6) == "$50-100M"
    assert deal_size_band(2e9) == "$500M+"


def test_canonicalize_deals_shapes_rows():
    raw = [{
        "deal_id": "12345-67T", "company": "Acme AI", "company_pbid": "999-88",
        "deal_date": "2026-09-01", "round_type": "Series A", "deal_size_usd": 25e6,
        "lead_investors": ["Sequoia"], "investors": ["Sequoia", "a16z"],
        "sector": "AI/ML", "themes": ["GenAI"], "region": "US",
        "is_apollo_relevant": True,
    }]
    df = canonicalize_deals(raw, "run-1")
    row = df.iloc[0]
    assert row["deal_id"] == "12345-67T"
    assert row["deal_size_band"] == "$10-50M"
    assert row["investors"] == "Sequoia, a16z"
    assert row["refresh_run_id"] == "run-1"
    assert bool(row["is_apollo_relevant"]) is True


def test_message_hash_stable_and_dedupes():
    a = message_hash("https://x.com/story", "guid-1")
    b = message_hash("https://x.com/story-DIFFERENT", "guid-1")
    assert a == b
    assert message_hash("https://x.com/a") != message_hash("https://x.com/b")


def test_local_cache_roundtrip(tmp_path, monkeypatch):
    """Cache miss → None; after a write the replica reads back (incl. all-None cols)."""
    monkeypatch.setenv("VC_CACHE_DIR", str(tmp_path))
    import os

    import pandas as pd
    import pyarrow as pa
    from deltalake import write_deltalake

    from data_access.dbtables.vc import local_cache

    assert local_cache.enabled()
    assert local_cache.read("vc_deals", "dev") is None  # nothing built yet

    df = pd.DataFrame({"deal_id": ["1", "2"], "sector": ["AI/ML", "Fintech"],
                       "sub_sector": [None, None]})  # all-None column
    path = local_cache._table_path("dev", "vc_deals")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tbl = pa.Table.from_pandas(df, preserve_index=False)
    for i, f in enumerate(tbl.schema):
        if pa.types.is_null(f.type):
            tbl = tbl.set_column(i, f.name, tbl.column(i).cast(pa.string()))
    write_deltalake(path, tbl, mode="overwrite")

    got = local_cache.read("vc_deals", "dev")
    assert got is not None and len(got) == 2 and set(got["deal_id"]) == {"1", "2"}


def test_fetch_full_extract_and_write(tmp_path):
    from data_importer.vc.news.fetch_full import extract_readable, write_raw_doc
    html = ("<html><head><title>Big Seed Round</title></head><body>"
            "<nav>menu junk</nav><article>"
            "<p>Acme raised $20M in a seed round led by Sequoia Capital.</p>"
            "<p>The round values the company at $100M and funds hiring.</p>"
            "</article><footer>footer junk</footer></body></html>")
    title, text = extract_readable(html)
    assert title == "Big Seed Round"
    assert "Sequoia Capital" in text
    assert "menu junk" not in text and "footer junk" not in text
    p = write_raw_doc(tmp_path, url="https://x.com/a", title=title, source="TechCrunch",
                      published_at="2026-09-18", body=text)
    assert p.exists() and p.name.startswith("20260918__TechCrunch__Big-Seed-Round__")
    body = p.read_text(encoding="utf-8")
    assert "Sequoia Capital" in body and "url: https://x.com/a" in body


def test_heatmap_table_builds_styles():
    hm = {"sectors": ["AI/ML", "Fintech"], "buckets": ["Seed", "Growth"],
          "matrix": [[3, 1], [0, 2]]}
    columns, data, styles = formatting.heatmap_table(hm)
    assert columns[0]["id"] == "sector"
    assert data[0]["sector"] == "AI/ML"
    assert data[0]["Seed"] == 3
    assert any(s["if"]["column_id"] == "Seed" for s in styles)
