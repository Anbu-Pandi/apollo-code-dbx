import logging

import numpy as np
import pandas as pd


VIEW_COLUMNS = [
    'position_row_id',
    'report_date',
    'publication_date',
    'period_type_code',
    'reporting_period_desc',
    'report_currency',
    'sec_accession_id',
    'bdc_factset_entity_id',
    'bdc_name_factset',
    'factset_report_id',
    'factset_portfolio_entity_id',
    'portfolio_name_factset',
    'fsym_id',
    'dcs_lookup_fsym_id',
    'factset_sym_cusip_loan_cusip',
    'factset_sym_cusip_loan_ppn_cusip',
    'factset_sym_cusip_loan_pcl_cusip',
    'factset_sym_cusip_hist_cusip',
    'factset_sym_isin_hist_isin',
    'factset_sym_bbg_bbg_id',
    'dcs_instrument_id_bb',
    'dcs_id_bb8',
    'sym_v1_instrument_id_bb',
    'sym_v1_id_bb8',
    'instrument_id_bb',
    'id_bb8',
    'bdc_security_desc',
    'investment_desc',
    'debt_code_description',
    'seniority_desc',
    'coupon_desc',
    'coupon_index',
    'min_coupon_rate',
    'min_coupon_margin',
    'pay_in_kind_coupon_rate',
    'instrument_currency',
    'issue_date',
    'acquisition_date',
    'maturity_date',
    'amount_issued',
    'par_value',
    'fair_value',
    'cost',
    'net_assets_pct',
    'fair_to_par_value_ratio',
    'convert_par_value_flag',
    'exchange_rate',
    'non_accrual_status',
    'defaulted_flag',
    'perpetual_flag',
    'convertible_flag',
    'pay_in_kind_flag',
    'terminated_flag',
    'dcs_detail_id',
    'dcs_report_date',
    'dcs_instrument_description',
    'dcs_debt_code',
    'dcs_debt_desc',
    'dcs_issue_type_code',
    'dcs_issue_type_desc',
    'dcs_seniority_code',
    'dcs_seniority_desc',
    'dcs_coupon_code',
    'dcs_coupon_index',
    'dcs_coupon_rate_min',
    'dcs_coupon_margin_min',
    'dcs_issue_date',
    'dcs_maturity_date',
    'dcs_issuance_currency',
    'dcs_currency',
    'dcs_issue_amount',
    'dcs_os_amount',
    'dcs_avail_amount',
    'dcs_borrowing_base',
    'dcs_isin',
    'dcs_bbg_id',
    'dcs_bbg_ticker',
    'dcs_joined_fsym_id',
    'dcs_join_source',
    'sp_matched',
    'sp_factset_key_col',
    'sp_factset_key_variant',
    'sp_index_key_col',
    'sp_index_key_variant',
    'sp_matched_key',
    'sp_asof_date',
    'sp_issuer',
    'sp_facility_name',
    'sp_narrow_industry',
    'jpm_matched',
    'jpm_factset_key_col',
    'jpm_factset_key_variant',
    'jpm_index_key_col',
    'jpm_index_key_variant',
    'jpm_matched_key',
    'jpm_asof_date',
    'jpm_issuer_name',
    'jpm_asset_name',
    'jpm_ind_segment',
    'ice_matched',
    'ice_factset_key_col',
    'ice_factset_key_variant',
    'ice_index_key_col',
    'ice_index_key_variant',
    'ice_matched_key',
    'ice_asof_date',
    'ice_index_ticker',
    'ice_description',
    'ice_ticker',
    'ice_industry_lvl4_desc',
    'ice_price',
    'ice_spread',
    'ice_yield',
    'ice_rating',
    'ice_amount_outstanding',
    'remark_source_table',
    'remark_price',
    'remark_fair_value_from_ice_price',
]


def _clean_key(series):
    s = series.astype(str).str.strip().str.upper()
    return s.where(~s.isin(['', 'NAN', 'NONE', 'NULL']), np.nan)


def _clean_bbg(series):
    return _clean_key(series)


def _variant_map(series):
    base = _clean_key(series)
    alnum = base.str.replace(r'[^A-Z0-9]', '', regex=True)
    alnum = alnum.where(alnum.str.len() > 0)
    has_prefix = alnum.str[:2].str.match(r'^[A-Z]{2}$', na=False)
    isin_body = alnum.str[2:].where(has_prefix)
    cusip9 = isin_body.str[:9]
    cusip9 = cusip9.where(cusip9.str.len() == 9)
    return {'raw': base, 'isin_to_cusip9': cusip9}


def _existing_cols(df, candidates):
    return [c for c in candidates if c in df.columns]


def _explode_key_variants(df, row_id_cols, key_cols, key_col_name, variant_col_name):
    parts = []
    for c in key_cols:
        for v_name, v_series in _variant_map(df[c]).items():
            part = df[row_id_cols].copy()
            part[key_col_name] = c
            part[variant_col_name] = v_name
            part['key_value'] = v_series.values
            part = part.loc[part['key_value'].notna()]
            if not part.empty:
                parts.append(part)
    if not parts:
        return pd.DataFrame(columns=row_id_cols + [key_col_name, variant_col_name, 'key_value'])
    return pd.concat(parts, ignore_index=True).drop_duplicates()


INDEX_MAP_CONFIG = {
    'sp_loan_index': {
        'key_cols': ['figi', 'figi_id_bb', 'figi_id_bb8', 'cusip', 'linid', 'loan_xid', 'id'],
        'enrich_cols': ['asof_date', 'issuer', 'facility_name', 'narrow_industry'],
        'bbg_source_col': 'figi',
        'prefix': 'sp',
        'enrich_rename': {
            'asof_date': 'sp_asof_date',
            'issuer': 'sp_issuer',
            'facility_name': 'sp_facility_name',
            'narrow_industry': 'sp_narrow_industry',
        },
    },
    'jpm_loan_index': {
        'key_cols': ['cusip', 'loan_id', 'bbg_id', 'bbg_id_bb', 'bbg_id_bb8', 'loanx', 'facility_id'],
        'enrich_cols': ['asof_date', 'issuer_name', 'asset_name', 'ind_segment'],
        'bbg_source_col': 'bbg_id',
        'prefix': 'jpm',
        'enrich_rename': {
            'asof_date': 'jpm_asof_date',
            'issuer_name': 'jpm_issuer_name',
            'asset_name': 'jpm_asset_name',
            'ind_segment': 'jpm_ind_segment',
        },
    },
    'ice_index_all': {
        'key_cols': ['cusip', 'cusip9', 'isin_number', 'ice_unique_id'],
        'enrich_cols': ['asof_date', 'ice_index_ticker', 'description', 'ticker', 'industry_lvl4_desc',
                        'price', 'spread', 'yield', 'rating', 'amount_outstanding'],
        'bbg_source_col': None,
        'prefix': 'ice',
        'enrich_rename': {
            'asof_date': 'ice_asof_date',
            'ice_index_ticker': 'ice_index_ticker',
            'description': 'ice_description',
            'ticker': 'ice_ticker',
            'industry_lvl4_desc': 'ice_industry_lvl4_desc',
            'price': 'ice_price',
            'spread': 'ice_spread',
            'yield': 'ice_yield',
            'rating': 'ice_rating',
            'amount_outstanding': 'ice_amount_outstanding',
        },
    },
}

FACTSET_MATCH_KEY_COLS = [
    'bbg_id_new8', 'bbg_id_new',
    'dcs_id_bb8', 'dcs_id_bb',
    'dcs_bbg_id', 'factset_sym_bbg_bbg_id',
    'dcs_isin', 'factset_sym_isin_hist_isin',
    'factset_sym_cusip_loan_cusip', 'factset_sym_cusip_loan_ppn_cusip',
    'factset_sym_cusip_loan_pcl_cusip', 'factset_sym_cusip_hist_cusip',
    'dcs_detail_id', 'dcs_joined_fsym_id', 'dcs_lookup_fsym_id',
    'fsym_id',
]


def enrich_bbg(df, bbg_bridge, bbg_col):
    if bbg_col is None or bbg_col not in df.columns:
        df['bbg_id_new'] = np.nan
        df['bbg_id_new8'] = np.nan
        return
    src = _clean_bbg(df[bbg_col])
    df['bbg_id_bb'] = _clean_bbg(src.map(bbg_bridge))
    df['bbg_id_bb8'] = df['bbg_id_bb'].str[:8]
    df.loc[df['bbg_id_bb8'].str.len() != 8, 'bbg_id_bb8'] = np.nan
    df['bbg_id_new'] = df['bbg_id_bb']
    df['bbg_id_new8'] = df['bbg_id_bb8']


def enrich_factset_bbg(factset_df, bbg_bridge):
    dcs_bbg = _clean_bbg(factset_df.get('dcs_bbg_id', pd.Series(dtype=str)))
    sym_bbg = _clean_bbg(factset_df.get('factset_sym_bbg_bbg_id', pd.Series(dtype=str)))

    factset_df['dcs_instrument_id_bb'] = _clean_bbg(dcs_bbg.map(bbg_bridge))
    factset_df['dcs_id_bb8'] = factset_df['dcs_instrument_id_bb'].str[:8]
    factset_df.loc[factset_df['dcs_id_bb8'].str.len() != 8, 'dcs_id_bb8'] = np.nan

    factset_df['sym_v1_instrument_id_bb'] = _clean_bbg(sym_bbg.map(bbg_bridge))
    factset_df['sym_v1_id_bb8'] = factset_df['sym_v1_instrument_id_bb'].str[:8]
    factset_df.loc[factset_df['sym_v1_id_bb8'].str.len() != 8, 'sym_v1_id_bb8'] = np.nan

    factset_df['instrument_id_bb'] = factset_df['dcs_instrument_id_bb'].fillna(factset_df['sym_v1_instrument_id_bb'])
    factset_df['id_bb8'] = factset_df['dcs_id_bb8'].fillna(factset_df['sym_v1_id_bb8'])

    factset_df['dcs_id_bb'] = factset_df['dcs_instrument_id_bb']
    factset_df['bbg_id_new'] = factset_df['instrument_id_bb']
    factset_df['bbg_id_new8'] = factset_df['id_bb8']


def match_indices(factset_df, index_dfs):
    index_id_maps = {}
    for tbl, cfg in INDEX_MAP_CONFIG.items():
        if tbl not in index_dfs:
            continue
        df = index_dfs[tbl].copy()
        key_cols = _existing_cols(df, cfg['key_cols'])
        enrich_cols = _existing_cols(df, cfg.get('enrich_cols', []))
        use_cols = key_cols + [c for c in enrich_cols if c not in key_cols]
        out = df[use_cols].copy() if use_cols else pd.DataFrame()
        out['index_table'] = tbl
        out['_index_row_id'] = np.arange(len(out))
        base_keys = _existing_cols(out, cfg['key_cols'])
        ext_keys = [k for k in ['bbg_id_new', 'bbg_id_new8'] if k in out.columns]
        cfg['effective_key_cols'] = list(dict.fromkeys(base_keys + ext_keys))
        index_id_maps[tbl] = out

    fs_key_cols = _existing_cols(factset_df, FACTSET_MATCH_KEY_COLS)
    fs_row_ids = ['report_date', 'bdc_name_factset', 'fsym_id']
    if 'asof_date' in factset_df.columns:
        fs_row_ids.append('asof_date')

    factset_key_long = _explode_key_variants(factset_df, fs_row_ids, fs_key_cols, 'factset_key_col', 'factset_variant')
    if 'asof_date' in factset_key_long.columns:
        factset_key_long = factset_key_long.rename(columns={'asof_date': 'bdc_asof_date'})

    idx_parts = []
    for tbl, cfg in INDEX_MAP_CONFIG.items():
        if tbl not in index_id_maps:
            continue
        m = index_id_maps[tbl]
        id_cols = ['_index_row_id', 'index_table']
        if 'asof_date' in m.columns:
            id_cols.append('asof_date')
        kl = _explode_key_variants(m, id_cols, cfg['effective_key_cols'], 'index_key_col', 'index_variant')
        if 'asof_date' in kl.columns:
            kl = kl.rename(columns={'asof_date': 'index_asof_date'})
        idx_parts.append(kl)

    index_key_long = pd.concat(idx_parts, ignore_index=True) if idx_parts else pd.DataFrame()
    all_matches = factset_key_long.merge(index_key_long, on='key_value', how='inner')

    if 'index_asof_date' in all_matches.columns and 'bdc_asof_date' in all_matches.columns:
        all_matches['bdc_asof_date'] = pd.to_datetime(all_matches['bdc_asof_date'])
        all_matches['index_asof_date'] = pd.to_datetime(all_matches['index_asof_date'])
        all_matches = all_matches[all_matches['index_asof_date'] <= all_matches['bdc_asof_date']]
    elif 'index_asof_date' in all_matches.columns:
        all_matches['report_date'] = pd.to_datetime(all_matches['report_date'])
        all_matches['index_asof_date'] = pd.to_datetime(all_matches['index_asof_date'])
        all_matches = all_matches[all_matches['index_asof_date'] <= all_matches['report_date']]

    logging.info(f'All key matches (asof filtered): {len(all_matches):,}')

    fs_pri = {k: i for i, k in enumerate(fs_key_cols)}
    idx_pri = {tbl: {k: i for i, k in enumerate(cfg['effective_key_cols'])} for tbl, cfg in INDEX_MAP_CONFIG.items() if tbl in index_id_maps}

    if all_matches.empty:
        return factset_df.copy(), pd.DataFrame()

    all_matches['fv_pri'] = all_matches['factset_variant'].map(lambda v: 0 if v == 'raw' else 1)
    all_matches['iv_pri'] = all_matches['index_variant'].map(lambda v: 0 if v == 'raw' else 1)
    all_matches['fk_pri'] = all_matches['factset_key_col'].map(lambda c: fs_pri.get(c, 999))
    all_matches['ik_pri'] = all_matches.apply(lambda r: idx_pri.get(r['index_table'], {}).get(r['index_key_col'], 999), axis=1)

    sort_cols = ['report_date', 'bdc_name_factset', 'fsym_id', 'index_table', 'fv_pri', 'iv_pri', 'fk_pri', 'ik_pri']
    if 'index_asof_date' in all_matches.columns:
        all_matches['index_asof_desc'] = -all_matches['index_asof_date'].astype('int64')
        sort_cols.append('index_asof_desc')

    best = (
        all_matches
        .sort_values(sort_cols + ['key_value'])
        .drop_duplicates(subset=['report_date', 'bdc_name_factset', 'fsym_id', 'index_table'], keep='first')
    )
    logging.info(f'Best matches: {len(best):,}')

    join_cols = ['report_date', 'bdc_name_factset', 'fsym_id']
    result = factset_df.copy()

    for tbl, cfg in INDEX_MAP_CONFIG.items():
        if tbl not in index_id_maps:
            continue
        prefix = cfg['prefix']
        hit = best.loc[best['index_table'] == tbl].copy()

        idx_map = index_id_maps[tbl]
        hit = hit.merge(idx_map, on='_index_row_id', how='left', suffixes=('', '_idx'))

        rename = {
            'factset_key_col': f'{prefix}_factset_key_col',
            'factset_variant': f'{prefix}_factset_key_variant',
            'index_key_col': f'{prefix}_index_key_col',
            'index_variant': f'{prefix}_index_key_variant',
            'key_value': f'{prefix}_matched_key',
        }
        hit = hit.rename(columns=rename)
        keep = join_cols + list(rename.values())

        enrich_rename = cfg.get('enrich_rename', {})
        for old_col, new_col in enrich_rename.items():
            if old_col in hit.columns:
                hit = hit.rename(columns={old_col: new_col})
                if new_col not in keep:
                    keep.append(new_col)

        hit = hit[_existing_cols(hit, keep)].drop_duplicates(subset=join_cols)
        result = result.merge(hit, on=join_cols, how='left')
        result[f'{prefix}_matched'] = result[f'{prefix}_matched_key'].notna()

    result['remark_source_table'] = np.where(
        result.get('ice_matched', False), 'ice_index_all',
        np.where(result.get('jpm_matched', False), 'jpm_loan_index',
        np.where(result.get('sp_matched', False), 'sp_loan_index', None)))
    result['remark_price'] = result.get('ice_price')
    ice_price = result.get('ice_price', pd.Series(dtype=float))
    par_value = result.get('par_value', pd.Series(dtype=float))
    result['remark_fair_value_from_ice_price'] = np.where(
        ice_price.notna() & par_value.notna(),
        par_value * (ice_price / 100.0),
        np.nan
    )

    return result, best


def finalize_result(df):
    df = df.copy()
    df = df.sort_values(['report_date', 'factset_report_id', 'factset_portfolio_entity_id', 'fsym_id', 'bdc_security_desc'])
    df = df.reset_index(drop=True)
    df['position_row_id'] = np.arange(1, len(df) + 1)

    for col in VIEW_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan

    return df[VIEW_COLUMNS]
