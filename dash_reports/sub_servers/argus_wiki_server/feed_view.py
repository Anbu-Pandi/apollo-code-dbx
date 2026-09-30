"""Argus Wiki v2 "HuggingNews" feed: the client-side layout (CSS/JS) plus the
server-side source enrichment (entities + preloaded synth summaries) and the
page-scoped semantic search. Extracted from reader.py so the v2 feed has one
home; reader.py imports this module and aliases the names it references.
"""
import html
import json
import logging
import re
from urllib.parse import unquote

import yaml

from argus_wiki.bootstrap import config
from argus_wiki.search import reader_contract

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


_SOURCE_FEED_CACHE: dict[str, dict] = {}


def _md_inline(text: str) -> str:
    """Escape a bullet line, then re-apply **bold** as <strong>."""
    esc = html.escape(text)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)


def _extract_thesis_html(body: str) -> str:
    """A compact synth summary for a source page. Prod source pages lead with a
    `> ...` blockquote summary before the first `##` heading; older dev pages use
    a `## Thesis`/`## Summary` bullet list. Handle both."""
    lines = body.splitlines()
    # (1) lead blockquote summary (prod): the contiguous `> ...` run before any `##`
    quote: list[str] = []
    started = False
    for ln in lines:
        st = ln.strip()
        if st.startswith("## "):
            break
        if st.startswith(">"):
            q = st.lstrip(">").strip()
            if q:
                quote.append(q)
                started = True
        elif started and st:
            break
    if quote:
        return "<p>" + " ".join(_md_inline(q) for q in quote[:4]) + "</p>"
    # (2) fallback: `## Thesis` / `## Summary` bullets (older dev format)
    bullets: list[str] = []
    in_thesis = False
    for ln in lines:
        st = ln.strip()
        if st.startswith("## "):
            if in_thesis:
                break
            in_thesis = st[3:].strip().lower() in ("thesis", "summary")
            continue
        if in_thesis and st.startswith("- "):
            bullets.append(st[2:].strip())
    if not bullets:
        return ""
    items = "".join("<li>" + _md_inline(b) + "</li>" for b in bullets[:6])
    return "<ul>" + items + "</ul>"


# source wikilinks are "sources/<optional YYYY-MM-DD bucket>/src_<hex>": prod
# buckets sources by day, dev is flat. Capture the stem regardless of the bucket.
_SOURCE_LINK_RE = re.compile(r"sources/(?:[^/\s|\]#]+/)?(src_[0-9a-fA-F]+)")


def _source_rels(text: str) -> dict:
    """{stem: full_rel} for every source cited in `text` (rel has no .md)."""
    out: dict[str, str] = {}
    for m in _SOURCE_LINK_RE.finditer(text or ""):
        out[m.group(1)] = m.group(0)
    return out


def _source_content_ts(fm: dict) -> str:
    """ISO timestamp used for the "Xh ago" chip on each source detail line.

    Prefers the email's content date (`day`), but that field is date-only, so we
    resolve intraday freshness from the processed/ES time (`ingested_at`):
    - same-day content -> use `ingested_at` (accurate hours/minutes ago);
    - older, back-filled content (content day < ingest day) -> anchor to the
      content day so the chip reads "N days ago", not a false "2h ago";
    - no `ingested_at` -> content day at noon.
    Returns "" when neither is present (chip is then omitted)."""
    ingested = str(fm.get("ingested_at") or "").strip()
    raw_day = str(fm.get("day") or "").strip()
    dnorm = f"{raw_day[:4]}-{raw_day[4:6]}-{raw_day[6:8]}" if len(raw_day) == 8 and raw_day.isdigit() else ""
    if ingested:
        if not dnorm or ingested[:10] <= dnorm:
            return ingested
        return dnorm + ingested[10:]               # older content: content day, ingest clock
    if dnorm:
        return dnorm + "T12:00:00"
    return ""


def _headline_tickers(fm: dict, title: str, summary: str) -> list[str]:
    """Tickers this source is really *about*, for trending — weighted toward the
    headline so multi-name morning notes don't dump their kitchen-sink lists:
    primary_ticker + tickers named in the TITLE rank first; tickers only in the
    summary are a fallback; first-few is the last resort. Capped."""
    allt = fm.get("tickers")
    if isinstance(allt, str):
        allt = [allt]
    allt = [str(t).strip() for t in allt if str(t).strip()] if isinstance(allt, list) else []

    def hit(tok: str, text: str) -> bool:
        return re.search(r"(?<![A-Za-z0-9.])" + re.escape(tok) + r"(?![A-Za-z0-9.])", text) is not None

    ents: list[str] = []
    pt = fm.get("primary_ticker")
    if isinstance(pt, str) and pt.strip():
        ents.append(pt.strip())
    for tok in allt:                               # headline: named in the title
        if tok not in ents and hit(tok, title):
            ents.append(tok)
    if not ents:                                   # fallback: named in the summary
        ents = [t for t in allt if hit(t, summary)] or allt[:3]
    return ents[:6]


def _source_feed_data(rel: str, *, cache_only: bool = False) -> dict:
    """{"e": [tickers], "s": "<ul> thesis summary </ul>"} for a source rel
    ("sources/<bucket>/src_xxx", no .md — bucketed on prod, flat on dev). Cached."""
    cached = _SOURCE_FEED_CACHE.get(rel)
    if cached is not None:
        return cached
    if cache_only:
        # Render hot path: NEVER a per-source SMB read. A today brief cites ~96
        # sources; reading each source .md over the share was ~76s (the feed_data
        # bucket). Cold sources just get no citation chip this render; the
        # background SWR revalidate (cache_only=False) reads + fills the cache, so
        # chips appear on the next render. Same rule as bulk source-titles.
        return {}
    data = {"e": [], "s": ""}
    text = ""
    for root in (config.WIKI_ROOT, reader_contract.default_view_wiki_root()):
        path = root / (rel + ".md")
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            break
    if text:
        fm: dict = {}
        body = text
        m = FRONTMATTER_RE.match(text)
        if m:
            try:
                fm = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError:
                fm = {}
            body = text[m.end():]
        summary = _extract_thesis_html(body)
        summary_text = re.sub(r"<[^>]+>", " ", summary)
        data = {
            "e": _headline_tickers(fm, str(fm.get("title") or ""), summary_text),
            "s": summary,
            "t": _source_content_ts(fm),
        }
    if len(_SOURCE_FEED_CACHE) > 4096:
        _SOURCE_FEED_CACHE.clear()
    _SOURCE_FEED_CACHE[rel] = data
    return data


def feed_source_data_script(note_text: str, *, cache_only: bool = False) -> str:
    """A `<script>` map {src_stem: {e:[tickers], s:summary_html}} for every source
    cited on a feed page — so the client feed can trend/filter on entities AND
    show each source's synth summary instantly (no per-click fetch)."""
    rels = _source_rels(note_text)
    if not rels:
        return ""
    data_map = {}
    for stem, rel in rels.items():
        d = _source_feed_data(rel, cache_only=cache_only)
        if d:
            data_map[stem] = d
    if not data_map:
        return ""
    return "<script>window.__FEED_SRC_DATA=" + json.dumps(data_map) + ";</script>"


_SOURCE_VEC_CACHE: dict[str, "object"] = {}   # stem -> normalized np.float32 vector


def _source_search_text(rel: str) -> str:
    """Plain-text synth summary for a source (Thesis bullets), for embedding."""
    data = _source_feed_data(rel)
    text = html.unescape(re.sub(r"<[^>]+>", " ", data.get("s") or "")).strip()
    return text or stem


def feed_semantic_search(view_name: str, rel: str, q: str) -> tuple[dict, int]:
    """Semantic rank of a feed page's cited sources against a free-text query,
    reusing the shared Argus Wiki embedder (argus_wiki.search.semantic_common).
    Returns {"results": [{"source_id": stem, "score": cosine}], "backend": ...}.
    Source vectors are embedded once and cached; only the query is embedded per
    call. Falls back (503) so the client can use its lexical filter if the
    embedder is unavailable."""
    q = (q or "").strip()
    if not q:
        return {"results": []}, 200
    clean = unquote(rel or "").split("#", 1)[0].replace("\\", "/").strip("/")
    if not clean or ".." in clean.split("/"):
        return {"error": "invalid rel"}, 400
    from dash_reports.sub_servers.argus_wiki_server import reader
    note = reader._load_preview_note(view_name, clean)
    if not note:
        return {"error": "not found"}, 404
    rels = _source_rels(note.text)
    stems = sorted(rels)
    if not stems:
        return {"results": []}, 200
    try:
        import numpy as np
        from argus_wiki.search import semantic_common
    except Exception as exc:
        logging.info("argus_wiki feed_semantic_search embedder unavailable: %s", exc)
        return {"error": "embedder unavailable"}, 503
    try:
        missing = [s for s in stems if s not in _SOURCE_VEC_CACHE]
        if missing:
            mat = np.asarray(semantic_common.embed_texts([_source_search_text(rels[s]) for s in missing]), dtype="float32")
            if mat.ndim == 2 and mat.shape[0] == len(missing):
                norms = np.linalg.norm(mat, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                mat = mat / norms
                for i, s in enumerate(missing):
                    _SOURCE_VEC_CACHE[s] = mat[i]
        qv = np.asarray(semantic_common.embed_texts([q])[0], dtype="float32")
        qn = float(np.linalg.norm(qv)) or 1.0
        qv = qv / qn
    except Exception as exc:
        logging.warning("argus_wiki feed_semantic_search embed failed: %s", exc)
        return {"error": "embed failed"}, 503
    results = []
    for s in stems:
        v = _SOURCE_VEC_CACHE.get(s)
        if v is None:
            continue
        results.append({"source_id": s, "score": round(float(np.dot(qv, v)), 4)})
    results.sort(key=lambda r: r["score"], reverse=True)
    return {"results": results, "backend": "embed:" + semantic_common.embed_model_name()}, 200


FEED_CSS = """
  /* ---- feed layout: single centered column, chrome trimmed ---- */
  body.layout-feed .rightbar { display: none; }
  body.layout-feed main { max-width: 860px; margin: 0 auto; width: 100%; }
  body.layout-feed .breadcrumb, body.layout-feed .meta-strip,
  body.layout-feed .page-mtime, body.layout-feed .tag-pills { display: none; }
  body.layout-feed article { font-size: 15px; }
  body.layout-feed article > h1 { display: none; }

  /* masthead: the ATI top band, compacted */
  .hn-masthead { padding: 6px 0 2px; }
  .hn-masthead p, .hn-masthead > * { margin: 2px 0; }
  .hn-masthead strong { font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: var(--text-muted); }
  .hn-masthead em { color: var(--text-muted); font-size: 13px; }
  .hn-masthead code { background: none; padding: 0; color: var(--text-muted); font-size: 11.5px; }

  /* TL;DR card (from the ICYMI abstract) */
  .hn-tldr { border-top: 2px solid var(--text); border-bottom: 1px solid var(--border-light);
             margin: 12px 0 4px; padding: 10px 0 12px; }
  .hn-tldr-label { font-size: 11px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase;
                   color: var(--text); display: flex; align-items: baseline; gap: 8px; }
  .hn-tldr-label .hn-tldr-ts { font-weight: 500; letter-spacing: 0; text-transform: none; color: var(--text-muted); font-size: 11px; }
  .hn-tldr ul { margin: 8px 0 0; padding: 0; list-style: none; columns: 2; column-gap: 28px; }
  .hn-tldr-prose { font-size: 13px; line-height: 1.65; max-width: 90ch; }
  .hn-tldr a { font-size: 11px; display: inline-block; margin: 3px 6px 0 0; }
  .hn-story-sources { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding-top: 12px; font-size: 12px; }
  @media (max-width: 720px) { .hn-tldr ul { columns: 1; } }
  .hn-tldr li { font-size: 13px; line-height: 1.5; margin: 0 0 7px; padding-left: 16px; text-indent: -16px;
                break-inside: avoid; color: var(--text); }
  .hn-tldr li::before { content: "›"; color: var(--accent, var(--apollo-green, #007B63)); font-weight: 700; margin-right: 8px; }

  /* sticky filter stack: topic pills */
  .hn-sticky { position: sticky; top: 0; z-index: 20; background: var(--bg, var(--surface, #fff));
               padding: 8px 0 6px; border-bottom: 1px solid var(--border-light); margin-bottom: 2px; }
  .hn-pills { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .hn-pill { padding: 3px 11px; border: 1px solid var(--border-light); border-radius: 999px; font-size: 12px;
             font-weight: 500; color: var(--text-muted); background: transparent; cursor: pointer;
             white-space: nowrap; transition: all .12s ease; user-select: none; }
  .hn-pill:hover { border-color: var(--border-strong, var(--border)); color: var(--text); }
  .hn-pill.active { background: var(--text); color: var(--bg, #fff); border-color: var(--text); font-weight: 600; }
  .hn-pill .hn-pill-count { opacity: .6; margin-left: 4px; font-variant-numeric: tabular-nums; }

  /* search box — free-text filter over the stories below */
  .hn-search { display: flex; align-items: center; gap: 10px; }
  .hn-search-input { flex: 1; width: 100%; padding: 8px 14px; font-family: var(--sans); font-size: 13px;
                     color: var(--text); background: var(--bg); border: 1px solid var(--border-light);
                     border-radius: 999px; outline: none;
                     transition: border-color .12s ease, background .12s ease, box-shadow .12s ease; }
  .hn-search-input:focus { border-color: var(--accent, var(--apollo-green, #007B63));
                           background: var(--surface); box-shadow: 0 0 0 3px rgba(0, 123, 99, .08); }
  .hn-search-input::placeholder { color: var(--text-muted); }
  .hn-search-note { font-size: 11.5px; color: var(--text-muted); white-space: nowrap; font-variant-numeric: tabular-nums; }

  /* trending tag strip (top of feed) — ranked by frequency across all stories */
  .hn-trending { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px; margin: 12px 0 2px; }
  .hn-trending-label { font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
                       color: var(--text-muted); margin-right: 4px; }
  .hn-trend-chip { padding: 2px 9px; border-radius: 999px; font-size: 12px; font-weight: 500; cursor: pointer;
                   background: var(--bg-elev, rgba(0,0,0,.04)); color: var(--text); border: 1px solid transparent;
                   white-space: nowrap; transition: all .12s ease; }
  .hn-trend-chip:hover { border-color: var(--border-strong, var(--border)); }
  .hn-trend-chip.active { background: var(--accent, var(--apollo-green, #007B63)); color: #fff; }
  .hn-trend-chip.active .hn-trend-n { opacity: .8; }
  .hn-trend-chip[data-kind="entity"] { font-weight: 700; }
  .hn-trend-chip[data-kind="theme"] { color: var(--text-muted); font-weight: 500; }
  .hn-trend-chip .hn-trend-n { opacity: .55; margin-left: 4px; font-variant-numeric: tabular-nums; }

  /* day heading */
  .hn-day { display: flex; align-items: baseline; gap: 10px; border-bottom: 1px solid var(--text);
            padding: 18px 0 6px; margin-top: 6px; }
  .hn-day-date { font-size: 16px; font-weight: 700; letter-spacing: -.01em; color: var(--text); }
  .hn-day-count { color: var(--text-muted); font-size: 11.5px; font-variant-numeric: tabular-nums; }

  /* the ranked list — reset the theme's default <details>/callout chrome so
     rows read as a dense HN list, not stacked cards */
  .hn-list { display: flex; flex-direction: column; }
  .hn-story { border: 0; border-bottom: 1px solid var(--border-light); background: none;
              margin: 0; padding: 0; border-radius: 0; box-shadow: none; }
  .hn-story[hidden] { display: none; }
  .hn-story > summary { list-style: none; cursor: pointer; margin: 0; padding: 0; background: none; border: 0; }
  .hn-story > summary::-webkit-details-marker { display: none; }
  .hn-story > summary::marker { content: none; }
  .hn-story > summary::before { display: none !important; content: none !important; }
  .hn-row { display: grid; grid-template-columns: 1.9rem minmax(0,1fr) auto; align-items: baseline;
            column-gap: 14px; padding: 8px 6px; transition: background .1s ease; position: relative; }
  .hn-row:hover { background: var(--row-hover, var(--bg-elev, rgba(0,0,0,.03))); }
  .hn-story[open] > summary .hn-row { background: var(--row-hover, var(--bg-elev, rgba(0,0,0,.03))); }
  .hn-rank { text-align: right; color: var(--text-muted); font-size: 13px; font-variant-numeric: tabular-nums; }
  .hn-story.is-top .hn-rank { color: var(--text); font-weight: 700; }
  .hn-story.is-top .hn-row::before { content: ""; position: absolute; left: 0; top: 22%; bottom: 22%;
            width: 2px; background: var(--accent, var(--apollo-green, #007B63)); }
  .hn-title { font-size: 15px; font-weight: 600; line-height: 1.34; letter-spacing: -.005em; color: var(--text); }
  .hn-title .hn-new { color: var(--accent, var(--apollo-green, #007B63)); font-size: 10.5px; font-weight: 700;
            letter-spacing: .06em; margin-right: 6px; vertical-align: 1px; }
  .hn-meta { display: grid; grid-template-columns: 9.5rem 4.8rem; gap: 0 12px; white-space: nowrap; align-items: baseline;
             font-size: 12.5px; }
  .hn-cat { color: var(--text); font-weight: 500; overflow: hidden; text-overflow: ellipsis; }
  .hn-time { color: var(--text-muted); font-variant-numeric: tabular-nums; min-width: 4.6rem; text-align: right; }
  .hn-time[data-fresh="hot"] { color: var(--accent, var(--apollo-green, #007B63)); font-weight: 700; }
  .hn-time[data-fresh="fresh"] { color: var(--text); font-weight: 600; }
  .hn-time[data-fresh="stale"] { color: var(--text-muted); opacity: .7; }
  .hn-score { color: var(--text); font-weight: 700; font-variant-numeric: tabular-nums; min-width: 2.6rem;
              text-align: right; }
  .hn-expanded { padding: 2px 6px 16px 2.15rem; font-size: 13.5px; line-height: 1.6; color: var(--text); }
  .hn-signal { font-size: 11.5px; color: var(--text-muted); text-transform: uppercase; letter-spacing: .04em;
               margin: 0 0 8px; }
  .hn-expanded .hn-thesis { font-size: 14px; line-height: 1.6; margin: 0 0 10px; }
  .hn-story-deck { display: -webkit-box; margin: 6px 0 12px; overflow: hidden; -webkit-box-orient: vertical;
                   -webkit-line-clamp: 3; font-size: 14px; line-height: 1.55; color: var(--text); }
  .hn-story-deck p { margin: 0; }
  .hn-story-deck strong { color: var(--text); }

  /* sources: each opens its own synth summary in place */
  .hn-sources { display: flex; flex-direction: column; margin: 2px 0 4px; }
  .hn-src { border-top: 1px solid var(--border-light); }
  .hn-src:last-child { border-bottom: 1px solid var(--border-light); }
  .hn-src > summary { list-style: none; cursor: pointer; display: flex; align-items: baseline; gap: 8px;
                      padding: 8px 2px; }
  .hn-src > summary::-webkit-details-marker { display: none; }
  .hn-src > summary::marker { content: none; }
  .hn-src > summary::before { content: "\\203A"; color: var(--text-muted); font-weight: 700;
                              transition: transform .12s ease; display: inline-block; }
  .hn-src[open] > summary::before { transform: rotate(90deg); }
  .hn-src-broker { font-weight: 600; font-size: 13px; color: var(--accent, var(--apollo-green, #007B63)); white-space: nowrap; }
  .hn-src-time { font-size: 11px; color: var(--text-muted); white-space: nowrap; font-variant-numeric: tabular-nums; flex: none; }
  .hn-src-time[data-fresh="hot"] { color: var(--accent, var(--apollo-green, #007B63)); font-weight: 600; }
  .hn-src-time[data-fresh="stale"] { opacity: .6; }
  .hn-src-ev { flex: 1 1 auto; min-width: 0; max-width: 100%; font-size: 12.5px; color: var(--text-muted);
               overflow: hidden; text-overflow: clip; white-space: nowrap; }
  .hn-src-track { display: flex; width: max-content; will-change: transform; }
  .hn-src-tick { flex: none; padding-right: 48px; }
  .hn-src-ev[data-ticker="true"] .hn-src-track {
    animation: hn-src-ticker var(--ticker-duration, 24s) linear infinite;
  }
  .hn-src-ev:hover .hn-src-track, .hn-src-ev:focus .hn-src-track { animation-play-state: paused; }
  @keyframes hn-src-ticker {
    0%, 10% { transform: translateX(0); }
    90%, 100% { transform: translateX(-50%); }
  }
  .hn-src-body { padding: 2px 2px 14px 20px; }
  .hn-src-summary { font-size: 13px; line-height: 1.58; color: var(--text); }
  .hn-src-summary ul { margin: 4px 0; padding-left: 16px; list-style: disc; }
  .hn-src-summary li { margin: 0 0 5px; padding: 0; text-indent: 0; }
  .hn-src-summary li::before { content: none; }
  .hn-src-loading, .hn-src-none { color: var(--text-muted); font-size: 12.5px; }
  .hn-src-open { display: inline-block; margin-top: 10px; font-size: 12px; font-weight: 700;
                 letter-spacing: .02em; color: var(--accent, var(--apollo-green, #007B63)); text-decoration: none; }
  .hn-src-open:hover { text-decoration: underline; }
  .hn-expanded ul { margin: 6px 0 0; padding-left: 0; list-style: none; }
  .hn-expanded ul li { margin: 0 0 8px; padding-left: 14px; text-indent: -14px; font-size: 13px; line-height: 1.55; color: var(--text-muted); }
  .hn-expanded ul li::before { content: "—"; color: var(--border-strong, var(--text-muted)); margin-right: 8px; }
  .hn-expanded a { color: var(--accent, var(--apollo-green, #007B63)); }
  .hn-expanded details { margin-top: 8px; }
  @media (max-width: 640px) {
    body.layout-feed main { max-width: 100%; }
    .hn-row { grid-template-columns: 1.4rem minmax(0,1fr); column-gap: 10px; }
    .hn-meta { grid-column: 2; grid-template-columns: auto auto; justify-content: start; margin-top: 3px; font-size: 11.5px; gap: 0 9px; }
    .hn-expanded { padding-left: 1.4rem; }
    .hn-tldr ul { columns: 1; }
  }
"""


FEED_JS = """
  <script>
  (function feedLayout() {
    if (!document.body.classList.contains("layout-feed")) return;
    var article = document.querySelector("article");
    if (!article) return;

    function txt(el) { return (el && el.textContent || "").trim(); }

    function headlineTicker(el) {
      var label = el.textContent;
      requestAnimationFrame(function () {
        if (el.scrollWidth <= el.clientWidth + 4) return;
        var track = document.createElement("span");
        track.className = "hn-src-track";
        [false, true].forEach(function (duplicate) {
          var item = document.createElement("span");
          item.className = "hn-src-tick";
          item.textContent = label;
          if (duplicate) item.setAttribute("aria-hidden", "true");
          track.appendChild(item);
        });
        el.textContent = "";
        el.appendChild(track);
        var distance = track.firstElementChild.scrollWidth;
        el.style.setProperty("--ticker-duration", Math.max(18, distance / 24 / 0.8).toFixed(1) + "s");
        el.dataset.ticker = "true";
      });
    }

    /* relative time from "2026-07-30 12:57 PM ET" or ISO */
    function relTime(s) {
      if (!s) return ["", "recent"];
      var m = s.match(/(\\d{4})-(\\d{2})-(\\d{2})(?:[ T](\\d{1,2}):(\\d{2}))?\\s*(AM|PM)?/i);
      if (!m) return [s, "recent"];
      var h = m[4] ? parseInt(m[4], 10) : 12, mi = m[5] ? parseInt(m[5], 10) : 0;
      if (m[6]) { var ap = m[6].toUpperCase(); if (ap === "PM" && h < 12) h += 12; if (ap === "AM" && h === 12) h = 0; }
      var d = new Date(parseInt(m[1],10), parseInt(m[2],10)-1, parseInt(m[3],10), h, mi);
      var secs = Math.floor((Date.now() - d.getTime()) / 1000);
      var fresh = "recent";
      if (secs < 6*3600) fresh = "hot"; else if (secs < 24*3600) fresh = "fresh";
      else if (secs > 5*86400) fresh = "stale";
      var label;
      if (secs < 0) label = "just now";
      else if (secs < 3600) label = Math.max(1, Math.floor(secs/60)) + "m ago";
      else if (secs < 86400) label = Math.floor(secs/3600) + "h ago";
      else label = Math.floor(secs/86400) + "d ago";
      return [label, fresh];
    }

    /* collect the sibling run for a heading up to the next H3/H2/HR */
    function groupFor(h3) {
      var nodes = [], sib = h3.nextElementSibling;
      while (sib && sib.tagName !== "H3" && sib.tagName !== "H2" && sib.tagName !== "H1") {
        var nxt = sib.nextElementSibling;
        if (sib.tagName === "HR") { sib._drop = true; nodes.push(sib); sib = nxt; continue; }
        nodes.push(sib); sib = nxt;
      }
      return nodes;
    }

    /* find the `_meta:_` paragraph in a node run */
    function metaPara(nodes) {
      for (var i = 0; i < nodes.length; i++) {
        var p = nodes[i];
        if (p.tagName !== "P") continue;
        var em = p.querySelector(":scope > em:first-child");
        var head = txt(p).slice(0, 6).toLowerCase();
        if ((em && em.textContent.trim().toLowerCase() === "meta:") || head.indexOf("meta:") === 0) return p;
      }
      return null;
    }

    /* turn an evidence <ul> into a list of click-to-open source summaries.
       The synth summary is preloaded server-side (window.__FEED_SRC_DATA), so
       opening a source is instant — no per-click fetch. */
    function buildSourceList(ul) {
      var items = Array.prototype.slice.call(ul.querySelectorAll(":scope > li"));
      function sourceLink(li) {
        return Array.prototype.find.call(li.querySelectorAll("a[href]"), function (a) {
          return /sources\\/(?:[^/]+\\/)?src_[0-9a-fA-F]+/.test(a.getAttribute("href"));
        });
      }
      if (!items.length || items.some(function (li) { return !sourceLink(li); })) return null;
      var wrap = document.createElement("div");
      wrap.className = "hn-sources";
      Array.prototype.forEach.call(ul.querySelectorAll(":scope > li"), function (li) {
        var a = sourceLink(li);
        if (!a) return;                                  /* not a source bullet */
        var url = a.getAttribute("href");
        var stemMatch = url.match(/sources\\/(?:[^/]+\\/)?(src_[0-9a-fA-F]+)/);
        var srcData = (stemMatch && SRC[stemMatch[1]]) || null;
        var summary = (srcData && srcData.s) || "";
        /* the linkout label is "<Sender> · <date> · <kind>"; show only the short
           sender as the linkout, and render the note's age as an "Xh ago" chip
           computed live from its content/processed timestamp — never a raw date. */
        var fullLabel = txt(a);
        var broker = fullLabel.split("\\u00b7")[0].trim() || fullLabel;
        var ev = txt(li).replace(fullLabel, "").replace(/^[\\s\\u2014\\-:\\u00b7]+/, "").trim();
        var ago = (srcData && srcData.t) ? relTime(srcData.t) : ["", "recent"];
        var det = document.createElement("details");
        det.className = "hn-src";
        var sm = document.createElement("summary"); sm.className = "hn-src-head";
        var b = document.createElement("span"); b.className = "hn-src-broker"; b.textContent = broker;
        sm.appendChild(b);
        if (ago[0]) { var ag = document.createElement("span"); ag.className = "hn-src-time"; ag.textContent = ago[0]; ag.dataset.fresh = ago[1]; sm.appendChild(ag); }
        if (ev) {
          var e = document.createElement("span"); e.className = "hn-src-ev"; e.textContent = "\\u2014 " + ev;
          e.tabIndex = 0; e.setAttribute("aria-label", "\\u2014 " + ev); sm.appendChild(e);
        }
        det.appendChild(sm);
        var body = document.createElement("div"); body.className = "hn-src-body";
        var slot = document.createElement("div"); slot.className = "hn-src-summary";
        slot.innerHTML = summary || li.innerHTML;
        body.appendChild(slot);
        var open = document.createElement("a"); open.className = "hn-src-open";
        open.href = url; open.textContent = "Read source and original article \\u2192";
        open.target = "_blank"; open.rel = "noopener";
        body.appendChild(open);
        det.appendChild(body);
        wrap.appendChild(det);
        if (ev) headlineTicker(e);
      });
      return wrap.children.length ? wrap : null;
    }

    /* ---- 1. pull the ICYMI abstract into a TL;DR card ---- */
    var tldr = null;
    var abstract = article.querySelector("details.callout-abstract");
    if (abstract) {
      tldr = document.createElement("div");
      tldr.className = "hn-tldr";
      var lbl = document.createElement("div");
      lbl.className = "hn-tldr-label";
      lbl.innerHTML = "TL;DR <span class='hn-tldr-ts'>today\\u2019s brief</span>";
      tldr.appendChild(lbl);
      var ul = abstract.querySelector("ul");
      if (ul) {
        tldr.appendChild(ul.cloneNode(true));
      } else {
        var built = document.createElement("div"); built.className = "hn-tldr-prose";
        Array.prototype.forEach.call(abstract.querySelectorAll("p"), function (p) {
          built.appendChild(p.cloneNode(true));
        });
        if (built.children.length) tldr.appendChild(built);
      }
      Array.prototype.forEach.call(tldr.querySelectorAll("a[href]"), function (a) {
        a.title = a.textContent;
        a.textContent = a.textContent.split("\\u00b7").slice(0, 2).map(function (part) { return part.trim(); }).join(" \\u00b7 ");
      });
      abstract.remove();
    }

    /* ---- 2. build story objects from the H3 blocks ---- */
    var SRC = window.__FEED_SRC_DATA || {};   /* {src_stem: {e:[tickers], s:summary_html}} server-side */
    var h3s = Array.prototype.slice.call(article.querySelectorAll("h3"));
    var stories = [];
    h3s.forEach(function (h3) {
      if (h3.closest("details") || h3.closest("blockquote")) return;
      var nodes = groupFor(h3);
      var mp = metaPara(nodes);

      /* score + tags from the H3 line ("10 / 10 · AI Infra · Industrials") */
      var h3text = txt(h3);
      var score = "", tags = [], headline = "";
      var segs = h3text.split("\\u00b7").map(function (x) { return x.trim(); }).filter(Boolean);
      var scoreMatch = h3text.match(/(\\d{1,2})\\s*\\/\\s*10/);
      if (scoreMatch) { score = scoreMatch[1] + "/10"; }
      segs.forEach(function (s) { if (!/\\d\\s*\\/\\s*10/.test(s) && !/^\\d+$/.test(s)) tags.push(s); });
      /* today-full schema: headline lives in the H3 link */
      var h3link = h3.querySelector("a");

      var updated = "", signal = "";
      if (mp) {
        var strongs = mp.querySelectorAll("strong");
        var firstStrong = strongs[0] ? strongs[0].textContent.trim() : "";
        if (/^\\d{1,2}\\/\\d{1,2}$/.test(firstStrong)) {
          if (!score) score = firstStrong;                 /* today-full: **8/10** */
          if (strongs[1]) tags.unshift(strongs[1].textContent.trim());
        } else if (firstStrong) {
          var headlineParts = firstStrong.split("\\u00b7").map(function (part) { return part.trim(); });
          headline = headlineParts[0];
          if (!tags.length && headlineParts.length > 1) tags.push(headlineParts[1]);
        }
        var mtext = txt(mp);
        var um = mtext.match(/Last updated:\\s*([^\\u00b7]+)/i);
        if (um) updated = um[1].trim();
        else { var dm = mtext.match(/(\\d{4}-\\d{2}-\\d{2}[^\\u00b7]*)/); if (dm) updated = dm[1].trim(); }
        var sm = mtext.match(/Signal:\\s*([^\\u00b7]+)/i);
        if (sm) signal = sm[1].trim();
      }
      if (!headline) headline = h3link ? txt(h3link) : (segs.length ? segs[segs.length - 1] : h3text);
      if (!tags.length && h3link) tags = ["Research"];

      /* entities = union of extracted tickers across this story's cited sources;
         also accrue each source's summary text into the story's search haystack */
      var entities = [], entSeen = {}, srcStems = [], stemSeen = {}, summaryText = "", deckHtml = "", freshTs = "", tmp = document.createElement("div");
      nodes.forEach(function (node) {
        if (node.nodeType === 1 &&
            (node.classList.contains("card-thesis") ||
             (node.tagName === "DETAILS" && (node.classList.contains("callout-summary") || node.classList.contains("callout-tldr"))))) {
          var deckNode = node.cloneNode(true);
          var deckLabel = deckNode.querySelector(":scope > summary");
          if (deckLabel) deckLabel.remove();
          deckHtml = deckNode.innerHTML;
        }
        if (!node.querySelectorAll) return;
        Array.prototype.forEach.call(node.querySelectorAll("a[href]"), function (a) {
          var m = (a.getAttribute("href") || "").match(/sources\\/(?:[^/]+\\/)?(src_[0-9a-fA-F]+)/);
          if (!m) return;
          if (!stemSeen[m[1]]) { stemSeen[m[1]] = 1; srcStems.push(m[1]); }
          var d = SRC[m[1]];
          if (!d) return;
          if (d.t && d.t > freshTs) freshTs = d.t;   /* story freshness = newest cited source */
          (d.e || []).forEach(function (t) { if (!entSeen[t]) { entSeen[t] = 1; entities.push(t); } });
          if (d.s) { tmp.innerHTML = d.s; summaryText += " " + (tmp.textContent || ""); }
        });
      });
      var searchText = (headline + " " + tags.join(" ") + " " + entities.join(" ") + " " + summaryText).toLowerCase();

      stories.push({
        h3: h3, nodes: nodes, mp: mp, score: score, scoreNum: scoreMatch ? parseInt(scoreMatch[1], 10) : (parseInt((score||"").split("/")[0], 10) || 0),
        tags: tags, headline: headline, updated: updated, signal: signal, cat: tags[0] || "Research",
        entities: entities, searchText: searchText, srcStems: srcStems, deckHtml: deckHtml, freshTs: freshTs
      });
    });

    if (!stories.length) return;   /* not a story page — leave it alone */

    /* ---- 3. masthead = everything before the first story block ---- */
    var firstH3 = stories[0].h3;
    var masthead = document.createElement("div");
    masthead.className = "hn-masthead";
    var pre = [], n = article.firstChild;
    while (n && n !== firstH3) { var nx = n.nextSibling; pre.push(n); n = nx; }
    pre.forEach(function (node) {
      if (node.nodeType === 1 && (node.classList.contains("day-digest"))) return;
      masthead.appendChild(node);
    });

    /* day label: compact + relative. The masthead dateline reads
       "Friday, July 31, 2026 · Coverage: ..." — strip that verbose weekday date out
       of the masthead (the divider carries the date) and render the divider as
       "Today" / "Yesterday" / "Jul 31", with the compact date beside the count. */
    var MON_FULL = { January:0,February:1,March:2,April:3,May:4,June:5,July:6,August:7,September:8,October:9,November:10,December:11 };
    var MON_ABBR = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    var dayLabel = "Latest", dayDate = "";
    var dm = txt(masthead).match(/([A-Za-z]+)\\s+(\\d{1,2}),\\s+(\\d{4})/);
    if (dm && (dm[1] in MON_FULL)) {
      var _mo = MON_FULL[dm[1]], _dd = parseInt(dm[2], 10), _yr = parseInt(dm[3], 10);
      dayDate = MON_ABBR[_mo] + " " + _dd;
      var _d0 = new Date(_yr, _mo, _dd), _now = new Date();
      var _diff = Math.round((new Date(_now.getFullYear(), _now.getMonth(), _now.getDate()) - _d0) / 86400000);
      dayLabel = _diff === 0 ? "Today" : (_diff === 1 ? "Yesterday" : dayDate);
    }
    var codeEl = masthead.querySelector("code");
    if (codeEl) codeEl.textContent = txt(codeEl).replace(/^\\s*[A-Za-z]+,\\s+[A-Za-z]+\\s+\\d{1,2},\\s+\\d{4}\\s*\\u00b7\\s*/, "");

    /* ---- 4. build the feed DOM ---- */
    var feed = document.createElement("div");
    feed.className = "hn-feed";
    feed.appendChild(masthead);
    if (tldr) feed.appendChild(tldr);

    /* ---- trending: blended entities + themes, weighted ~70% entities / 30% themes ---- */
    function countAcross(key) {
      var counts = {}, order = [];
      stories.forEach(function (s) {
        (s[key] || []).forEach(function (t) {
          if (!t) return;
          if (!(t in counts)) { counts[t] = 0; order.push(t); }
          counts[t]++;
        });
      });
      order.sort(function (a, b) { return counts[b] - counts[a]; });
      return { counts: counts, order: order };
    }
    var entAgg = countAcross("entities");
    var themeAgg = countAcross("tags");
    /* a couple of top entities + more themes, then interspersed by relative
       importance (story count) — entity and theme counts share the same scale */
    var chips = [];
    entAgg.order.slice(0, 2).forEach(function (t) { chips.push({ kind: "entity", val: t, n: entAgg.counts[t] }); });
    themeAgg.order.slice(0, 8).forEach(function (t) { chips.push({ kind: "theme", val: t, n: themeAgg.counts[t] }); });
    chips.sort(function (a, b) { return b.n - a.n; });
    var trending = null;
    if (chips.length) {
      trending = document.createElement("div");
      trending.className = "hn-trending";
      var tlab = document.createElement("span");
      tlab.className = "hn-trending-label"; tlab.textContent = "Trending";
      trending.appendChild(tlab);
      chips.forEach(function (c) {
        var chip = document.createElement("button");
        chip.className = "hn-trend-chip"; chip.dataset.kind = c.kind; chip.dataset.val = c.val;
        chip.appendChild(document.createTextNode(c.val));
        var tn = document.createElement("span"); tn.className = "hn-trend-n"; tn.textContent = c.n;
        chip.appendChild(tn);
        trending.appendChild(chip);
      });
      feed.appendChild(trending);
    }

    /* ---- search box: free-text filter over the stories below (title, themes,
       entities, and each source's synth summary) ---- */
    var sticky = document.createElement("div");
    sticky.className = "hn-sticky";
    var searchWrap = document.createElement("div");
    searchWrap.className = "hn-search";
    var searchInput = document.createElement("input");
    searchInput.type = "search";
    searchInput.className = "hn-search-input";
    searchInput.setAttribute("placeholder", "Search these stories \\u2014 company, theme, number\\u2026");
    searchInput.setAttribute("aria-label", "Search stories");
    searchWrap.appendChild(searchInput);
    var searchNote = document.createElement("span");
    searchNote.className = "hn-search-note";
    searchWrap.appendChild(searchNote);
    sticky.appendChild(searchWrap);
    feed.appendChild(sticky);

    var day = document.createElement("div");
    day.className = "hn-day";
    day.innerHTML = "<span class='hn-day-date'></span><span class='hn-day-count'></span>";
    day.querySelector(".hn-day-date").textContent = dayLabel;
    day.querySelector(".hn-day-count").textContent =
      (dayDate && dayLabel !== dayDate ? dayDate + " \\u00b7 " : "") + stories.length + " stories";
    feed.appendChild(day);

    var list = document.createElement("div");
    list.className = "hn-list";
    feed.appendChild(list);

    var storyEls = [];   /* {det, text} for filtering */
    stories.forEach(function (s, idx) {
      var rank = idx + 1;
      var det = document.createElement("details");
      det.className = "hn-story" + ((s.scoreNum >= 9 || rank <= 3) ? " is-top" : "");
      det.dataset.cat = s.cat;
      det.dataset.tags = (s.tags || []).join("|");
      det.dataset.entities = (s.entities || []).join("|");

      var sum = document.createElement("summary");
      var row = document.createElement("div");
      row.className = "hn-row";

      var rk = document.createElement("div");
      rk.className = "hn-rank"; rk.textContent = rank;

      var title = document.createElement("div");
      title.className = "hn-title";
      var rt = relTime(s.freshTs || s.updated);
      if (rt[1] === "hot") { var nw = document.createElement("span"); nw.className = "hn-new"; nw.textContent = "NEW"; title.appendChild(nw); }
      title.appendChild(document.createTextNode(s.headline));

      var meta = document.createElement("div");
      meta.className = "hn-meta";
      var cat = document.createElement("span"); cat.className = "hn-cat"; cat.textContent = s.cat;
      var tm = document.createElement("span"); tm.className = "hn-time"; tm.textContent = rt[0]; tm.dataset.fresh = rt[1];
      meta.appendChild(cat); meta.appendChild(tm);
      /* score kept internally for ranking / is-top; the X/10 badge is intentionally not rendered */

      row.appendChild(rk); row.appendChild(title); row.appendChild(meta);
      sum.appendChild(row);
      det.appendChild(sum);

      var exp = document.createElement("div");
      exp.className = "hn-expanded";
      if (s.deckHtml) {
        var deck = document.createElement("div");
        deck.className = "hn-story-deck";
        deck.innerHTML = s.deckHtml;
        exp.appendChild(deck);
      }
      s.nodes.forEach(function (node) {
        if (node._drop) return;
        if (node === s.mp) return;
        if (node.nodeType === 1 && node.tagName === "P" && !txt(node)) return;
        if (node.nodeType === 1 &&
            (node.classList.contains("card-thesis") ||
             (node.tagName === "DETAILS" && (node.classList.contains("callout-summary") || node.classList.contains("callout-tldr"))))) {
          return;
        }
        /* evidence list -> click-to-open source summaries */
        if (node.nodeType === 1 && node.tagName === "UL") {
          var srcs = buildSourceList(node);
          if (srcs) { exp.appendChild(srcs); return; }
        }
        exp.appendChild(node);
      });
      var sourceNav = document.createElement("nav"); sourceNav.className = "hn-story-sources";
      sourceNav.setAttribute("aria-label", "Story sources");
      var sourceLabel = document.createElement("strong"); sourceLabel.textContent = "Sources"; sourceNav.appendChild(sourceLabel);
      var seenSources = {};
      Array.prototype.forEach.call(exp.querySelectorAll("a[href]"), function (a) {
        var href = a.getAttribute("href");
        if (!/sources\\/(?:[^/]+\\/)?src_[0-9a-fA-F]+/.test(href) || seenSources[href]) return;
        seenSources[href] = true;
        var link = a.cloneNode(true); link.title = a.textContent;
        link.textContent = "Source " + Object.keys(seenSources).length;
        link.target = "_blank"; link.rel = "noopener"; sourceNav.appendChild(link);
      });
      if (sourceNav.children.length > 1) exp.appendChild(sourceNav);
      det.appendChild(exp);
      list.appendChild(det);
      storyEls.push({ det: det, text: s.searchText || "", stems: s.srcStems || [] });
    });

    /* ---- 5. swap the article content ---- */
    article.innerHTML = "";
    article.appendChild(feed);

    /* ---- 6. filtering — trending chips (entity/theme) and the search box ---- */
    function setCount(shown, suffix) {
      day.querySelector(".hn-day-count").textContent = shown + (suffix || " stories");
    }
    function showAll() {
      storyEls.forEach(function (o) { o.det.hidden = false; });
      setCount(storyEls.length, " stories");
    }
    function filterBy(pred, suffix) {
      var shown = 0;
      storyEls.forEach(function (o) { var ok = pred(o); o.det.hidden = !ok; if (ok) shown++; });
      setCount(shown, suffix);
    }
    function resetActive(container, sel) {
      if (container) Array.prototype.forEach.call(container.querySelectorAll(sel), function (n) { n.classList.remove("active"); });
    }

    if (trending) {
      trending.addEventListener("click", function (ev) {
        var chip = ev.target.closest && ev.target.closest(".hn-trend-chip");
        if (!chip) return;
        var wasActive = chip.classList.contains("active");
        resetActive(trending, ".hn-trend-chip");
        searchInput.value = ""; searchNote.textContent = "";
        if (wasActive) { showAll(); return; }
        chip.classList.add("active");
        var val = chip.dataset.val, isEnt = chip.dataset.kind === "entity";
        var needle = "|" + val + "|";
        filterBy(function (o) {
          var d = o.det.dataset;
          return (("|" + ((isEnt ? d.entities : d.tags) || "") + "|").indexOf(needle) >= 0);
        }, " in " + val);
      });
    }

    function restoreOrder() {
      storyEls.forEach(function (o) { o.det.hidden = false; list.appendChild(o.det); });
      setCount(storyEls.length, " stories");
    }
    function lexicalSearch(q) {
      var toks = q.split(/\\s+/).filter(Boolean);
      var shown = 0;
      storyEls.forEach(function (o) {
        var ok = toks.every(function (t) { return o.text.indexOf(t) >= 0; });
        o.det.hidden = !ok; if (ok) shown++;
      });
      searchNote.textContent = shown + " match" + (shown === 1 ? "" : "es");
      setCount(shown, ' matching \\u201c' + q + '\\u201d');
    }
    searchInput.addEventListener("input", function () {
      var q = searchInput.value.trim();
      resetActive(trending, ".hn-trend-chip");
      if (!q) { searchNote.textContent = ""; restoreOrder(); return; }
      lexicalSearch(q.toLowerCase());
    });
  })();
  </script>
"""


# Non-feed brief pages (today.md) keep their prose layout, but their per-note
# source bullets get the SAME treatment as the feed: the linkout collapses to the
# short sender, and the absolute note date is replaced by a live "Xh ago" chip
# computed from window.__FEED_SRC_DATA (injected for brief pages too). This keeps
# today and today_v2 visually consistent without re-laying-out today.
SOURCE_CITE_CSS = """
  .cite-ago { font-weight: 400; font-size: 11px; color: var(--text-muted); margin-left: 6px;
              white-space: nowrap; font-variant-numeric: tabular-nums; }
  .cite-ago[data-fresh="hot"] { color: var(--accent, var(--apollo-green, #007B63)); font-weight: 600; }
  .cite-ago[data-fresh="stale"] { opacity: .6; }
"""

SOURCE_CITE_JS = """
  <script>
  (function citeRewrite() {
    if (document.body.classList.contains("layout-feed")) return;   /* feed owns its own */
    var SRC = window.__FEED_SRC_DATA;
    if (!SRC) return;
    var article = document.querySelector("article");
    if (!article) return;

    function relTime(s) {
      if (!s) return ["", "recent"];
      var m = s.match(/(\\d{4})-(\\d{2})-(\\d{2})(?:[ T](\\d{1,2}):(\\d{2}))?\\s*(AM|PM)?/i);
      if (!m) return ["", "recent"];
      var h = m[4] ? parseInt(m[4], 10) : 12, mi = m[5] ? parseInt(m[5], 10) : 0;
      if (m[6]) { var ap = m[6].toUpperCase(); if (ap === "PM" && h < 12) h += 12; if (ap === "AM" && h === 12) h = 0; }
      var d = new Date(parseInt(m[1],10), parseInt(m[2],10)-1, parseInt(m[3],10), h, mi);
      var secs = Math.floor((Date.now() - d.getTime()) / 1000);
      var fresh = "recent";
      if (secs < 6*3600) fresh = "hot"; else if (secs < 24*3600) fresh = "fresh";
      else if (secs > 5*86400) fresh = "stale";
      var label;
      if (secs < 0) label = "just now";
      else if (secs < 3600) label = Math.max(1, Math.floor(secs/60)) + "m ago";
      else if (secs < 86400) label = Math.floor(secs/3600) + "h ago";
      else label = Math.floor(secs/86400) + "d ago";
      return [label, fresh];
    }

    Array.prototype.forEach.call(article.querySelectorAll('a[href*="sources/"]'), function (a) {
      var url = a.getAttribute("href") || "";
      var m = url.match(/sources\\/(?:[^/]+\\/)?(src_[0-9a-fA-F]+)/);
      if (!m) return;
      var label = (a.textContent || "").trim();
      if (label.indexOf("\\u00b7") < 0) return;      /* only citation-style "Sender · date · kind" labels */
      var sender = label.split("\\u00b7")[0].trim();
      if (!sender) return;
      a.textContent = sender;                        /* linkout = short sender only */
      var d = SRC[m[1]];
      var rt = (d && d.t) ? relTime(d.t) : ["", "recent"];
      if (!rt[0]) return;
      var chip = document.createElement("span");
      chip.className = "cite-ago"; chip.textContent = rt[0]; chip.dataset.fresh = rt[1];
      a.insertAdjacentElement("afterend", chip);
    });
  })();
  </script>
"""
