import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser

import pandas as pd
import requests


DBPEDIA_ENDPOINT = "https://dbpedia.org/sparql"
WIKI_API = "https://en.wikipedia.org/w/api.php"
UA = "IndustryClassifier/1.0 (industry-classification-project)"
PAGE_SIZE = 5000
WIKI_BATCH_SIZE = 20
LLM_BATCH_SIZE = 50
LLM_WORKERS = 5
CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
LLM_MODEL = os.environ.get("AZURE_OAI_MODEL", "gpt-4.1")

_llm_client = None
_llm_deployment = None


def _get_llm_client():
    global _llm_client, _llm_deployment
    if _llm_client is None:
        from genai.common.llm_clients import create_llm_client
        _llm_client, _llm_deployment = create_llm_client(LLM_MODEL, timeout=120.0)
    return _llm_client, _llm_deployment


def _run_sparql(query: str, retries: int = 3) -> list:
    for attempt in range(retries):
        resp = requests.get(
            DBPEDIA_ENDPOINT,
            params={"query": query, "format": "json"},
            headers={"User-Agent": UA},
            timeout=120,
        )
        resp.raise_for_status()
        bindings = resp.json()["results"]["bindings"]
        if bindings:
            return bindings
        logging.warning(f"SPARQL attempt {attempt + 1} empty, retrying...")
        if attempt < retries - 1:
            time.sleep(2 ** attempt)
    return []


STRIP_SUFFIXES = [
    ", Inc.", " Inc.", " Inc", ", LLC", " LLC", ", Ltd.", " Ltd.", " Ltd",
    " Corporation", " Corp.", " Corp", " Co.", " Co", " Group", " Holdings",
    " Holding", " Limited", " S.A.", " S.p.A.", " PLC", " plc", " AG",
    " GmbH", " N.V.", " SE",
]


def _name_variants(name: str) -> list:
    variants = [name]
    for suffix in STRIP_SUFFIXES:
        if name.endswith(suffix):
            stripped = name[:-len(suffix)].strip()
            if stripped and stripped not in variants:
                variants.append(stripped)
    return variants


def _name_match(company_name: str, wiki_title: str) -> bool:
    cn = company_name.lower()
    wt = wiki_title.lower()
    if wt in cn or cn in wt:
        return True
    core = cn
    for suffix in STRIP_SUFFIXES:
        if core.endswith(suffix.lower()):
            core = core[:-len(suffix)].strip()
    if len(core) >= 6 and (core in wt or wt in core):
        return True
    cn_words = set(w for w in core.split() if len(w) >= 3)
    wt_words = set(w for w in wt.split() if len(w) >= 3)
    overlap = cn_words & wt_words
    return len(overlap) >= 2 and len(overlap) / max(len(cn_words), 1) >= 0.6


def _wiki_search_one(name: str) -> tuple:
    for variant in _name_variants(name):
        try:
            resp = requests.get(WIKI_API, params={
                "action": "query", "list": "search", "srsearch": variant,
                "srlimit": "3", "format": "json",
            }, headers={"User-Agent": UA}, timeout=15)
            hits = resp.json().get("query", {}).get("search", [])
            for hit in hits:
                if _name_match(name, hit["title"]):
                    return hit["title"], name
        except (requests.RequestException, OSError):
            pass
    return None, name


def _wiki_extract_batch(titles: list) -> dict:
    return _wiki_batch(titles)


def fetch_wiki_search_descriptions(names: list, workers: int = 20) -> pd.DataFrame:
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, "wiki_search_descriptions.parquet")

    cached = pd.DataFrame(columns=["name", "description"])
    if os.path.exists(cache_path):
        cached = pd.read_parquet(cache_path)

    cached_names = set(cached["name"].tolist())
    missing = [n for n in names if n not in cached_names]

    if not missing:
        return cached[cached["name"].isin(names)].reset_index(drop=True)

    logging.info(f"Wikipedia search for {len(missing)} companies ({workers} threads)...")
    title_to_name = {}
    done = 0

    def _search(name):
        title, orig = _wiki_search_one(name)
        return title, orig

    chunk_size = 500
    for chunk_start in range(0, len(missing), chunk_size):
        chunk = missing[chunk_start:chunk_start + chunk_size]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_search, n): n for n in chunk}
            for future in as_completed(futures):
                try:
                    title, orig = future.result()
                    if title:
                        title_to_name[title] = orig
                except Exception:
                    pass
                done += 1

        logging.info(f"  Wiki search: {done}/{len(missing)} done, {len(title_to_name)} matched")
        time.sleep(0.5)

    logging.info(f"  Wiki search matched: {len(title_to_name)}/{len(missing)} companies")

    matched_names = set(title_to_name.values())
    new_rows = []
    titles_list = list(title_to_name.keys())
    for i in range(0, len(titles_list), WIKI_BATCH_SIZE):
        batch = titles_list[i:i + WIKI_BATCH_SIZE]
        extracts = _wiki_batch(batch)
        for title, text in extracts.items():
            orig_name = title_to_name.get(title)
            if orig_name:
                new_rows.append({"name": orig_name, "description": text})
        time.sleep(0.3)

    for name in missing:
        if name not in matched_names:
            new_rows.append({"name": name, "description": ""})

    if new_rows:
        new_df = pd.DataFrame(new_rows).drop_duplicates(subset=["name"], keep="first")
        cached = pd.concat([cached, new_df], ignore_index=True).drop_duplicates(subset=["name"])
        cached.to_parquet(cache_path)
        hits = cached[cached["description"].str.len() > 50]
        logging.info(f"Wiki search cache: {len(cached)} total, {len(hits)} with descriptions")

    result = cached[cached["name"].isin(names)]
    return result[result["description"].str.len() > 50].reset_index(drop=True)


LLM_PROMPT = """For each company below, provide a 1-2 sentence description of what the company does, its industry, and products/services.
If the name suggests a financial instrument (CLO, fund, mezzanine, etc.) describe the type of financial product.
Format each line exactly as: COMPANY_NAME ||| DESCRIPTION

Companies:
"""


def _llm_describe_batch(names: list, retries: int = 3) -> dict:
    client, deployment = _get_llm_client()
    prompt = LLM_PROMPT + "\n".join(f"- {n}" for n in names)
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=deployment,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=len(names) * 150,
            )
            results = {}
            for line in resp.choices[0].message.content.strip().split("\n"):
                if "|||" in line:
                    name_part, desc = line.split("|||", 1)
                    desc = desc.strip()
                    name_part = name_part.strip().lstrip("- ")
                    if desc and len(desc) > 20:
                        results[name_part] = desc
            return results
        except Exception as e:
            logging.warning(f"LLM batch attempt {attempt + 1} failed: {e}")
            time.sleep(2 ** attempt)
    return {}


def _save_cache(cache_path: str, cached: pd.DataFrame, new_rows: list) -> pd.DataFrame:
    if not new_rows:
        return cached
    new_df = pd.DataFrame(new_rows).drop_duplicates(subset=["name"], keep="first")
    cached = pd.concat([cached, new_df], ignore_index=True).drop_duplicates(subset=["name"])
    cached.to_parquet(cache_path)
    return cached


def fetch_llm_descriptions(names: list, workers: int = LLM_WORKERS,
                           batch_size: int = LLM_BATCH_SIZE) -> pd.DataFrame:
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, "llm_descriptions.parquet")

    cached = pd.DataFrame(columns=["name", "description"])
    if os.path.exists(cache_path):
        cached = pd.read_parquet(cache_path)

    cached_names = set(cached["name"].tolist())
    missing = [n for n in names if n not in cached_names]

    if not missing:
        return cached[cached["name"].isin(names)].reset_index(drop=True)

    logging.info(f"LLM descriptions for {len(missing)} companies ({workers} threads, batch={batch_size})...")
    batches = [missing[i:i + batch_size] for i in range(0, len(missing), batch_size)]
    done_count = 0
    total_hits = 0

    for chunk_start in range(0, len(batches), workers):
        chunk = batches[chunk_start:chunk_start + workers]
        new_rows = []

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_llm_describe_batch, batch): batch for batch in chunk}
            for future in as_completed(futures):
                try:
                    results = future.result()
                    for name, desc in results.items():
                        new_rows.append({"name": name, "description": desc})
                except Exception as e:
                    logging.warning(f"LLM batch failed: {e}")

        cached = _save_cache(cache_path, cached, new_rows)
        done_count += len(chunk)
        total_hits += len(new_rows)
        companies_done = min(done_count * batch_size, len(missing))
        logging.info(f"  LLM: {companies_done}/{len(missing)} queried, {total_hits} descriptions")

    logging.info(f"LLM descriptions complete: {total_hits}/{len(missing)} new descriptions")
    return cached[cached["name"].isin(names)].reset_index(drop=True)


def _wiki_batch(titles: list, retries: int = 5) -> dict:
    params = {
        "action": "query",
        "titles": "|".join(titles),
        "prop": "extracts",
        "exintro": "1",
        "explaintext": "1",
        "exlimit": str(len(titles)),
        "redirects": "1",
        "format": "json",
    }
    for attempt in range(retries):
        resp = requests.get(WIKI_API, params=params, headers={"User-Agent": UA}, timeout=30)
        if resp.status_code == 429:
            wait = 2 ** attempt
            logging.warning(f"Wiki rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {})
        result = {}
        for page in pages.values():
            extract = page.get("extract", "")
            if extract and len(extract) > 50:
                result[page["title"]] = extract[:3000]
        return result
    return {}


def fetch_wiki_descriptions(names: list) -> pd.DataFrame:
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, "wiki_descriptions.parquet")

    cached = pd.DataFrame(columns=["name", "description"])
    if os.path.exists(cache_path):
        cached = pd.read_parquet(cache_path)

    cached_names = set(cached["name"].tolist())
    missing = [n for n in names if n not in cached_names]

    if missing:
        all_variants = {}
        for name in missing:
            for v in _name_variants(name):
                all_variants.setdefault(v, name)

        titles_to_fetch = list(all_variants.keys())
        logging.info(f"Fetching {len(titles_to_fetch)} Wikipedia titles for {len(missing)} companies...")

        new_rows = []
        for i in range(0, len(titles_to_fetch), WIKI_BATCH_SIZE):
            batch = titles_to_fetch[i:i + WIKI_BATCH_SIZE]
            matches = _wiki_batch(batch)
            for title, text in matches.items():
                original = all_variants.get(title, title)
                new_rows.append({"name": original, "description": text})
            if (i // WIKI_BATCH_SIZE + 1) % 25 == 0:
                logging.info(f"  Wiki progress: {i + len(batch)}/{len(titles_to_fetch)}")
            time.sleep(0.3)

        if new_rows:
            new_df = pd.DataFrame(new_rows).drop_duplicates(subset=["name"], keep="first")
            cached = pd.concat([cached, new_df], ignore_index=True).drop_duplicates(subset=["name"])
            cached.to_parquet(cache_path)
            logging.info(f"Cached {len(cached)} total descriptions")

    return cached[cached["name"].isin(names)].reset_index(drop=True)


def fetch_companies_with_industries(max_companies: int = 10000) -> pd.DataFrame:
    cache_path = os.path.join(CACHE_DIR, f"dbpedia_companies_{max_companies}.parquet")
    if os.path.exists(cache_path):
        logging.info(f"Loading cached DBpedia companies from {cache_path}")
        return pd.read_parquet(cache_path)

    logging.info(f"Fetching company names + industries from DBpedia (max={max_companies})")
    all_rows = []
    offset = 0

    while offset < max_companies:
        limit = min(PAGE_SIZE, max_companies - offset)
        query = f"""
        SELECT DISTINCT ?name
          (GROUP_CONCAT(DISTINCT REPLACE(STR(?industry), "http://dbpedia.org/resource/", ""); separator="|") AS ?industries)
        WHERE {{
          ?company a dbo:Company .
          ?company rdfs:label ?name .
          ?company dbo:industry ?industry .
          FILTER (lang(?name) = "en")
        }}
        GROUP BY ?name
        ORDER BY ?name
        OFFSET {offset}
        LIMIT {limit}
        """
        rows = _run_sparql(query)
        if not rows:
            break

        for r in rows:
            industries = r["industries"]["value"].replace("_", " ")
            for ind in industries.split("|"):
                all_rows.append({
                    "name": r["name"]["value"],
                    "industry_raw": ind.strip(),
                })

        logging.info(f"Fetched page at offset={offset}, total rows so far: {len(all_rows)}")
        offset += limit
        time.sleep(0.3)

    df = pd.DataFrame(all_rows).drop_duplicates(subset=["name", "industry_raw"])
    logging.info(f"Unique companies: {df['name'].nunique()}, total rows: {len(df)}")

    os.makedirs(CACHE_DIR, exist_ok=True)
    df.to_parquet(cache_path)
    logging.info(f"Cached DBpedia companies to {cache_path}")
    return df


def fetch_training_data(max_companies: int = 10000) -> pd.DataFrame:
    companies_df = fetch_companies_with_industries(max_companies)
    unique_names = companies_df["name"].unique().tolist()

    wiki_df = fetch_wiki_descriptions(unique_names)
    logging.info(f"Wiki descriptions loaded: {len(wiki_df)} rows")

    df = companies_df.merge(wiki_df, on="name", how="inner")
    df = df.dropna(subset=["description"])
    df = df[df["description"].str.len() > 100]
    logging.info(f"Training data ready: {len(df)} rows, {df['name'].nunique()} unique companies")
    return df


BBG_REF_COLS = [
    "id_bb_company", "long_comp_name", "cntry_of_domicile",
    "classification_level_name1", "classification_level_name2",
    "classification_level_code1", "classification_level_code2",
]

BBG_REF_SQL = f"""
SELECT {', '.join(BBG_REF_COLS)}
FROM dbo.v_bond_issuer_master
"""


def fetch_bbg_reference(force_refresh: bool = False) -> pd.DataFrame:
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, "bbg_issuer_ref.parquet")

    if not force_refresh and os.path.exists(cache_path):
        df = pd.read_parquet(cache_path)
        logging.info(f"BBG reference loaded from cache: {len(df)} companies")
        return df

    from common.utils.sqlutil import readdfsql
    logging.info("Fetching BBG issuer reference from Databricks...")
    df = readdfsql(BBG_REF_SQL, app="databricks")
    df = df.drop_duplicates(subset=["id_bb_company"], keep="last")
    df = df.dropna(subset=["long_comp_name"])
    df.to_parquet(cache_path)
    logging.info(f"BBG reference cached: {len(df)} companies")
    return df
