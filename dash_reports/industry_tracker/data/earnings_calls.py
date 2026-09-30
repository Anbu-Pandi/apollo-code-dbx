import logging
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from data_importer.factset_vector_api.vector_api import FactSetVectorAPI
from dash_reports.industry_tracker.data.universe import Industry

_VERSION_RE = re.compile(r'-[rt]_')


DISCOVERY_QUERY = 'first quarter earnings results revenue and full-year guidance'

POSITIVE_QUERY = (
    'positive momentum accelerating demand record growth raised guidance '
    'margin expansion strong product launches new market share wins '
    'innovation pipeline pricing power exceeded expectations beat consensus'
)
NEGATIVE_QUERY = (
    'negative pressures declines softening demand lowered guidance '
    'tariff drag FX headwind input cost inflation supply chain '
    'competitive pressure missed expectations disappointing performance'
)
GUIDANCE_QUERY = (
    'forward guidance outlook full-year revenue and EPS forecast '
    'raised lowered maintained reaffirmed reduced suspended guidance range '
    'fiscal year next quarter management expects guidance update'
)

DISCOVERY_WORKERS = 12
CONTENT_WORKERS = 8
CONTENT_OVERFETCH = 6
DATE_WINDOW_DAYS = 3
MAX_COMMENTARY_ISSUERS = 20
CLASSIFY_BATCH_SIZE = 270
CLASSIFY_WORKERS = 4

COMMENTARY_THEMES = [
    'Tariffs',
    'Middle East Conflict',
    'Commodity & Energy Costs',
    'Currency / FX',
    'Consumer Weakness',
    'Supply Chain',
    'Geographic Expansion',
    'Product Innovation',
    'E-commerce',
    'Premium / Luxury',
    'DTC / Direct Channel',
    'Market Share Gains',
]


def _section_key(vector_id: str) -> str:
    return _VERSION_RE.sub('_', vector_id or '')


def _dedupe_chunks(chunks: list) -> list:
    seen, out = set(), []
    for c in chunks:
        key = _section_key(c.get('vectorId') or '')
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _discover_one(api: FactSetVectorAPI, ticker_region: str, start: date, end: date) -> Optional[pd.Timestamp]:
    try:
        resp = api.get_vector_data(
            query=DISCOVERY_QUERY,
            ids=[ticker_region],
            document_types=['Transcripts'],
            sources=['FCST'],
            start_date=start,
            end_date=end,
            number_of_results=1,
        )
    except Exception as exc:
        logging.warning(f'discover failed for {ticker_region}: {exc}')
        return None
    rows = (resp or {}).get('data') or []
    if not rows:
        return None
    return pd.to_datetime(rows[0].get('storyDate'), errors='coerce')


def discover_recent_reporters(industry: Industry, days_back: int = 30,
                              api: Optional[FactSetVectorAPI] = None) -> pd.DataFrame:
    api = api or FactSetVectorAPI()
    end = date.today()
    start = end - timedelta(days=days_back)

    rows = []
    universe = []
    for sg, tr, name in industry.all_tickers():
        if tr and tr != '???':
            universe.append((sg, tr, name))

    logging.info(f'discover_recent_reporters: industry={industry.key} universe={len(universe)} window={start}..{end}')

    with ThreadPoolExecutor(max_workers=DISCOVERY_WORKERS) as ex:
        futs = {ex.submit(_discover_one, api, tr, start, end): (sg, tr, name)
                for sg, tr, name in universe}
        for f in as_completed(futs):
            sg, tr, name = futs[f]
            d = f.result()
            if d is None or pd.isna(d):
                continue
            rows.append({
                'sub_group': sg,
                'ticker_region': tr,
                'ticker': tr.split('-')[0],
                'name': name,
                'call_date': d.date(),
            })

    df = pd.DataFrame(rows).sort_values('call_date', ascending=False).reset_index(drop=True) \
        if rows else pd.DataFrame(columns=['sub_group', 'ticker_region', 'ticker', 'name', 'call_date'])
    logging.info(f'discover_recent_reporters: found {len(df)} issuers with transcripts in window')
    return df


def _fetch_chunks_for_query(api: FactSetVectorAPI, ticker: str, query: str,
                            call_date: date, n: int) -> list:
    start = call_date - timedelta(days=DATE_WINDOW_DAYS)
    end = call_date + timedelta(days=DATE_WINDOW_DAYS)
    try:
        resp = api.get_vector_data(
            query=query,
            ids=[ticker],
            document_types=['Transcripts'],
            sources=['FCST'],
            start_date=start,
            end_date=end,
            number_of_results=n,
        )
    except Exception as exc:
        logging.warning(f'content fetch failed for {ticker} on {call_date}: {exc}')
        return []
    return (resp or {}).get('data') or []


def _select_buckets(pos_meta: list, neg_meta: list, n: int) -> tuple:
    pos_meta = _dedupe_chunks(pos_meta)
    neg_meta = _dedupe_chunks(neg_meta)
    pos_scores = {_section_key(m.get('vectorId')): float(m.get('similarityScore') or 0.0) for m in pos_meta}
    neg_scores = {_section_key(m.get('vectorId')): float(m.get('similarityScore') or 0.0) for m in neg_meta}
    used = set()
    pos_pick, neg_pick = [], []
    for m in pos_meta:
        key = _section_key(m.get('vectorId'))
        if key in used or len(pos_pick) >= n:
            continue
        if neg_scores.get(key, 0.0) > pos_scores.get(key, 0.0):
            continue
        used.add(key)
        pos_pick.append(m)
    for m in neg_meta:
        key = _section_key(m.get('vectorId'))
        if key in used or len(neg_pick) >= n:
            continue
        used.add(key)
        neg_pick.append(m)
    return pos_pick, neg_pick


def _select_guidance(guide_meta: list, used_keys: set, n: int) -> list:
    out = []
    for m in _dedupe_chunks(guide_meta):
        key = _section_key(m.get('vectorId'))
        if key in used_keys:
            continue
        used_keys.add(key)
        out.append(m)
        if len(out) >= n:
            break
    return out


def _fetch_one_issuer(api: FactSetVectorAPI, row: dict, n_per_bucket: int) -> list:
    ticker = row['ticker_region']
    call_date = pd.to_datetime(row['call_date']).date()
    overfetch = n_per_bucket + CONTENT_OVERFETCH
    pos_meta = _fetch_chunks_for_query(api, ticker, POSITIVE_QUERY, call_date, overfetch)
    neg_meta = _fetch_chunks_for_query(api, ticker, NEGATIVE_QUERY, call_date, overfetch)
    guide_meta = _fetch_chunks_for_query(api, ticker, GUIDANCE_QUERY, call_date, n_per_bucket + CONTENT_OVERFETCH)
    pos_pick, neg_pick = _select_buckets(pos_meta, neg_meta, n_per_bucket)
    used_keys = {_section_key(m.get('vectorId')) for m in (pos_pick + neg_pick)}
    guide_pick = _select_guidance(guide_meta, used_keys, n_per_bucket)

    vector_ids = [m.get('vectorId') for m in (pos_pick + neg_pick + guide_pick) if m.get('vectorId')]
    text_map = {}
    if vector_ids:
        try:
            chunks = api.get_chunk_text(vector_ids)
            for c in (chunks or {}).get('data') or []:
                text_map[c.get('vectorId')] = c
        except Exception as exc:
            logging.warning(f'chunk-text fetch failed for {ticker}: {exc}')

    out = []
    for bucket, items in (('positive', pos_pick), ('negative', neg_pick), ('guidance', guide_pick)):
        for rank, m in enumerate(items, start=1):
            vid = m.get('vectorId')
            chunk = text_map.get(vid) or {}
            out.append({
                'sub_group': row['sub_group'],
                'ticker_region': ticker,
                'ticker': row['ticker'],
                'name': row['name'],
                'call_date': call_date,
                'bucket': bucket,
                'rank': rank,
                'similarity': float(m.get('similarityScore') or 0.0),
                'speaker': chunk.get('speakerName') or '',
                'content': chunk.get('content') or '',
                'story_link': chunk.get('storyLink') or '',
                'document_id': m.get('documentID') or chunk.get('documentID') or '',
                'vector_id': vid,
            })
    return out


def fetch_call_quotes(recent_df: pd.DataFrame, n_per_bucket: int = 4,
                      api: Optional[FactSetVectorAPI] = None) -> pd.DataFrame:
    if recent_df is None or recent_df.empty:
        return pd.DataFrame(columns=['sub_group', 'ticker_region', 'ticker', 'name',
                                     'call_date', 'bucket', 'rank', 'similarity',
                                     'speaker', 'content', 'story_link', 'document_id', 'vector_id'])
    api = api or FactSetVectorAPI()
    rows = recent_df.to_dict(orient='records')
    logging.info(f'fetch_call_quotes: issuers={len(rows)} n_per_bucket={n_per_bucket}')

    out = []
    with ThreadPoolExecutor(max_workers=CONTENT_WORKERS) as ex:
        futs = {ex.submit(_fetch_one_issuer, api, r, n_per_bucket): r for r in rows}
        for f in as_completed(futs):
            out.extend(f.result())

    df = pd.DataFrame(out)
    logging.info(f'fetch_call_quotes: returned {len(df)} chunks across {df["ticker_region"].nunique() if len(df) else 0} issuers')
    return df


def fetch_earnings_calls(industry: Industry, days_back: int = 30,
                         n_per_bucket: int = 4) -> pd.DataFrame:
    api = FactSetVectorAPI()
    recent = discover_recent_reporters(industry, days_back=days_back, api=api)
    return fetch_call_quotes(recent, n_per_bucket=n_per_bucket, api=api)


def _clean_text(text: str, n: int = 420) -> str:
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    return text if len(text) <= n else text[:n].rsplit(' ', 1)[0] + '...'


def _chunk_id(item: dict) -> str:
    vid = item.get('vector_id') or item.get('vectorId') or ''
    if vid:
        return _section_key(vid)
    return f'{item.get("ticker_region")}|{item.get("bucket")}|{item.get("rank")}'


def _latest_issuer_payload(df: pd.DataFrame) -> list:
    if df is None or df.empty:
        return []
    rows = []
    ordered = df.sort_values(['call_date', 'ticker_region', 'bucket', 'rank'],
                             ascending=[False, True, True, True])
    for _, grp in ordered.groupby('ticker_region', sort=False):
        first = grp.iloc[0]
        pos = grp[grp['bucket'] == 'positive'].sort_values('rank').head(3).to_dict('records')
        neg = grp[grp['bucket'] == 'negative'].sort_values('rank').head(3).to_dict('records')
        guide = (grp[grp['bucket'] == 'guidance'].sort_values('rank').head(3).to_dict('records')
                 if 'guidance' in grp['bucket'].values else [])
        chunks = [
            {'id': _chunk_id(it), 'retrieval_bucket': it.get('bucket'),
             'text': _clean_text(it.get('content'))}
            for it in (pos + neg + guide)
        ]
        rows.append({
            'ticker': first.get('ticker'),
            'ticker_region': first.get('ticker_region'),
            'company': first.get('name'),
            'sub_group': first.get('sub_group'),
            'call_date': str(first.get('call_date')),
            'chunks': chunks,
        })
        if len(rows) >= MAX_COMMENTARY_ISSUERS:
            break
    return rows


def _strip_fences(text: str) -> str:
    text = (text or '').strip()
    if '```json' in text:
        text = text.split('```json', 1)[1].split('```', 1)[0].strip()
    elif '```' in text:
        parts = text.split('```')
        if len(parts) >= 3:
            text = parts[1].strip()
    start, end = text.find('{'), text.rfind('}')
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return re.sub(r',\s*([}\]])', r'\1', text)


def _escape_inner_quotes(text: str) -> str:
    out = []
    in_string = False
    escape_next = False
    i = 0
    while i < len(text):
        ch = text[i]
        if escape_next:
            out.append(ch)
            escape_next = False
            i += 1
            continue
        if ch == '\\':
            out.append(ch)
            escape_next = True
            i += 1
            continue
        if ch == '"':
            if not in_string:
                in_string = True
                out.append(ch)
            else:
                lookahead = text[i + 1:i + 8]
                if re.match(r'\s*[,}\]:\n]', lookahead) or i == len(text) - 1:
                    in_string = False
                    out.append(ch)
                else:
                    out.append('\\"')
            i += 1
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _extract_json_object(text: str) -> dict:
    cleaned = _strip_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        try:
            return json.loads(_escape_inner_quotes(cleaned))
        except json.JSONDecodeError:
            try:
                import tempfile
                fp = tempfile.NamedTemporaryFile(prefix='earnings_commentary_raw_',
                                                 suffix='.json', delete=False, mode='w',
                                                 encoding='utf-8')
                fp.write(text)
                fp.close()
                logging.error(f'JSON parse failed at {exc.pos}: dumped raw response to {fp.name}')
            except Exception:
                pass
            raise


def _build_genie_client():
    from genai.argus_genies.constants import resolve_model_name
    from genai.sql_genie.clients.custom_genie import CustomGenieClient

    client = CustomGenieClient(
        use_system_prompt=False,
        model_deployment_name=resolve_model_name('low'),
        enable_notetaker=False,
        enable_learning=False,
    )
    client._skip_system_check = True
    return client


def _classify_payload(batch: list) -> list:
    prompt = {
        'task': 'Classify each transcript chunk by sentiment and lift the verbatim phrases that justify the classification.',
        'rules': [
            'sentiment must be exactly one of: positive | negative | neutral | guidance | mixed.',
            'positive: clear bullish news (beats, growth, momentum, share gains).',
            'negative: clear bearish news (misses, declines, headwinds, weakness).',
            'guidance: forward-looking outlook statements (raised, maintained, lowered, suspended).',
            'neutral: informational/contextual without a clear positive or negative lean.',
            'mixed: genuinely two-sided (both positive and negative drivers in the same chunk).',
            'highlights: return 1-3 short phrases (3-10 words each) lifted verbatim from the chunk text. Match the original casing exactly so they can be located in the source. Strip leading/trailing quotation marks; replace any internal double quote with a single quote.',
            'Output strict JSON only. Every string must be a valid JSON string with proper escaping; no line breaks inside string values.',
        ],
        'schema': {
            '<chunk_id>': {
                'sentiment': 'positive|negative|neutral|guidance|mixed',
                'highlights': ['verbatim phrase', 'verbatim phrase'],
            }
        },
        'chunks': batch,
    }
    return [
        {'role': 'system', 'content': 'You are a sentiment classifier for earnings call transcripts. Return strict JSON.'},
        {'role': 'user', 'content': json.dumps(prompt, default=str, ensure_ascii=False)},
    ]


def _classify_batch(batch: list, label: str) -> dict:
    client = _build_genie_client()
    response = client._call_llm(_classify_payload(batch),
                                timer_label=f'IndustryTrackerEarningsClassify:{label}')
    return _extract_json_object(response)


def classify_all_chunks(df: pd.DataFrame) -> dict:
    if df is None or df.empty:
        return {}
    items = []
    for rec in df.to_dict('records'):
        cid = _chunk_id(rec)
        if not cid:
            continue
        items.append({
            'id': cid,
            'ticker': rec.get('ticker') or '',
            'retrieval_bucket': rec.get('bucket') or '',
            'text': _clean_text(rec.get('content')),
        })
    if not items:
        return {}

    batches = [items[i:i + CLASSIFY_BATCH_SIZE]
               for i in range(0, len(items), CLASSIFY_BATCH_SIZE)]
    logging.info(f'classify_all_chunks: {len(items)} chunks across {len(batches)} batches')

    out: dict = {}
    with ThreadPoolExecutor(max_workers=min(CLASSIFY_WORKERS, len(batches))) as ex:
        futs = {ex.submit(_classify_batch, b, f'{i + 1}of{len(batches)}'): i
                for i, b in enumerate(batches)}
        for f in as_completed(futs):
            idx = futs[f]
            try:
                result = f.result()
            except Exception:
                logging.exception(f'classification batch {idx + 1} failed')
                continue
            if isinstance(result, dict):
                out.update(result)
            else:
                logging.warning(f'batch {idx + 1} returned non-dict: {type(result).__name__}')

    logging.info(f'classify_all_chunks: classified {len(out)}/{len(items)} chunks')
    return out


def generate_earnings_commentary(df: pd.DataFrame) -> dict:
    issuers = _latest_issuer_payload(df)
    if not issuers:
        return {'theme_takeaways': {}, 'segment_commentary': {}, 'issuer_commentary': {}, 'chunk_meta': {}}

    client = _build_genie_client()
    prompt = {
        'task': 'Create concise Apollo-style consumer earnings commentary from FactSet evidence.',
        'style_reference': [
            'Universal pressure but well-mitigated; companies offsetting via pricing and sourcing diversification.',
            'Promotional intensity and markdowns concentrated in sportswear and legacy apparel; staples holding firm.',
            'APAC the standout; structural offset to softer North America discretionary demand.',
        ],
        'rules': [
            'Use only facts and numbers present in evidence.',
            'Write analyst synthesis, not copied transcript snippets.',
            'Keep each theme_takeaway to one sentence under 34 words.',
            'Keep each segment_commentary to one punchy sentence under 38 words.',
            'Issuer bullets should be short fragments, not full transcript quotes.',
            'Mention tickers where useful.',
            'Return strict JSON only. Every string value must be a valid JSON string with double quotes properly escaped.',
        ],
        'themes': COMMENTARY_THEMES,
        'schema': {
            'theme_takeaways': {'Theme name': 'one sentence'},
            'segment_commentary': {'sub_group': 'one sentence'},
            'issuer_commentary': {
                'ticker_region': {
                    'guidance': 'raised|maintained|lowered|suspended|neutral',
                    'positive_bullets': ['up to four bullets'],
                    'negative_bullets': ['up to four bullets'],
                    'guidance_detail': 'one sentence',
                    'drivers': 'one sentence',
                    'headwinds': 'one sentence',
                    'geographies': 'one sentence',
                    'segments': 'one sentence',
                }
            },
        },
        'evidence': issuers,
    }
    messages = [
        {'role': 'system', 'content': 'You are an Apollo consumer sector analyst writing compact earnings-pulse commentary.'},
        {'role': 'user', 'content': json.dumps(prompt, default=str, ensure_ascii=False)},
    ]
    response = client._call_llm(messages, timer_label='IndustryTrackerEarningsCommentary')
    commentary = _extract_json_object(response)
    commentary['chunk_meta'] = classify_all_chunks(df)
    commentary['source'] = 'FactSet CallStreet Transcripts via Vector API'
    commentary['issuer_count'] = int(df['ticker_region'].nunique() if df is not None and len(df) else 0)
    commentary['snippet_count'] = int(len(df) if df is not None else 0)
    commentary['asof'] = date.today().isoformat()
    return commentary
