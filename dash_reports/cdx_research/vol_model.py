"""The AG notebook's volatility decomposition as explicit, staged functions.

Annual price vols are decimals. CDX/rate break-evens are bps/day.
Market-source mark precedence and duration normalization follow the notebook.
"""
import logging

import numpy as np
import pandas as pd
from scipy.stats import norm

from dash_reports.cdx_research.pipeline import lines, table_panel

TENOR_DAYS = {'1m': 21, '3m': 63, '6m': 126}
PAIRS = {'HY': ('HYG UP', 'CDX-NAHY', 'USD.* 5y'),
         'IG': ('LQD UP', 'CDX-NAIG', 'USD.* 10y')}
RETURN_IDS = {'HYG UP': 'HYG', 'LQD UP': 'LQD', 'SPX': 'SPX', 'IWM UP': 'IWM',
              'CDX-NAHY': 'CDX-NAHY', 'CDX-NAIG': 'CDX-NAIG',
              'USD.* 5y': 'tsy_5y', 'USD.* 10y': 'tsy_10y'}


def clean_dates(frame):
    frame = frame.copy()
    for col in ('asof_date', 'option_tenor_date'):
        if col in frame:
            frame[col] = pd.to_datetime(frame[col]).dt.normalize()
    return frame


def normalize_equity(frame):
    from financepy.models.black_scholes_analytic import bs_delta
    from financepy.utils.global_types import OptionTypes
    frame = clean_dates(frame)
    keys = ['bbg_id', 'asof_date', 'option_tenor_date']
    atm = frame[(frame.strike_reference == 'forward') & (frame.relative_strike == 1)]
    forward = atm.groupby(keys).absolute_strike.mean().rename('atm_forward_price')
    frame = frame.drop(columns='atm_forward_price', errors='ignore').merge(forward, on=keys, how='left')
    t = (frame.option_tenor_date - frame.asof_date).dt.days / 360
    frame['relative_strike_orig'] = frame.relative_strike
    frame['relative_strike'] = -bs_delta(frame.atm_forward_price.to_numpy(), t.to_numpy(),
                                        frame.absolute_strike.to_numpy(), 0., 0.,
                                        frame.implied_volatility.to_numpy(), OptionTypes.EUROPEAN_PUT.value)
    frame['price_vol'] = frame.implied_volatility
    return frame


def normalize_credit(frame):
    frame = clean_dates(frame.rename(columns={
        'option_end_date': 'option_tenor_date', 'tenor': 'option_tenor',
        'rel_strike': 'relative_strike', 'strike': 'absolute_strike',
        'implied_vol': 'implied_volatility', 'implied_normal_vol': 'be',
        'index_name': 'bbg_id', 'strike_type': 'strike_reference'}))
    forward = frame.apo_atm_fwd_spread.combine_first(frame.atm_forward_spread)
    frame['be'] = frame.implied_volatility * forward / np.sqrt(252)
    frame['price_vol'] = frame.implied_volatility * forward * frame.duration / frame.atm_forward_price / 100
    return frame


def normalize_rates(frame, curves):
    from pricing_wrapper.utils.date_funcs import tofindate
    frame = clean_dates(frame.rename(columns={
        'rel_strike': 'relative_strike2', 'strike': 'absolute_strike',
        'implied_normal_vol': 'be', 'annuity': 'duration', 'swap_name': 'bbg_id'}))
    sofr = frame.groupby('asof_date').bbg_id.transform(lambda s: s.str.contains('SOFR').any())
    frame = frame[sofr == frame.bbg_id.str.contains('SOFR')].copy()
    dates = sorted((pd.Timestamp(k), k) for k in curves)
    discounts = {}
    for asof, expiry in frame[['asof_date', 'option_tenor_date']].drop_duplicates().itertuples(index=False, name=None):
        candidates = [key for day, key in dates if day <= asof]

        discounts[(asof, expiry)] = curves[candidates[-1]].df(tofindate(expiry)) if candidates else np.nan
    t = (frame.option_tenor_date - frame.asof_date).dt.days / 360
    vol = frame.be * np.sqrt(252) / 1e4
    mult = np.where(frame.bbg_id.str.contains('Payer', case=False), 1, -1)
    discount = np.array([discounts[(a, e)] for a, e in frame[['asof_date', 'option_tenor_date']].itertuples(index=False, name=None)])
    with np.errstate(divide='ignore', invalid='ignore'):
        delta = mult * discount * norm.cdf(mult * (frame.atmf_rate - frame.absolute_strike) / (vol * np.sqrt(t)))
    frame['relative_strike'] = np.where(delta < 0, 1 + delta, delta)
    frame['price_vol'] = frame.be * np.sqrt(252) * frame.duration.abs() / 1e4
    frame['strike_reference'] = 'delta'
    return frame


def interpolate_delta(frame, delta, column):
    if column not in frame:
        return np.nan
    points = frame[['relative_strike', column]].apply(pd.to_numeric, errors='coerce').dropna()
    points = points.drop_duplicates('relative_strike').sort_values('relative_strike')
    if points.empty or not points.relative_strike.min() <= delta <= points.relative_strike.max():
        return np.nan
    return float(np.interp(delta, points.relative_strike, points[column]))


def implied_corr(frame, deltas, instruments):
    frame = frame.copy()
    frame['duration'] = frame.duration.abs()
    result = {}
    groups = {i: frame[frame.bbg_id.str.fullmatch(i)] for i in instruments}
    for instrument, group in groups.items():
        for field in ('duration', 'spot_price', 'spot_spread', 'atmf_rate'):
            result[f'{instrument}_{field}'] = interpolate_delta(group, .5, field)
    for delta in deltas:
        adjusted = []
        label = f'{delta * 100:g}'
        for instrument, group in groups.items():
            vol = interpolate_delta(group, delta, 'price_vol')
            result[f'{instrument}_{label}_unadj_price_vol'] = vol
            result[f'{instrument}_{label}_quoted_vol'] = interpolate_delta(group, delta, 'be')
            duration = result[f'{instrument}_duration']
            adj = vol * result[f'{instruments[0]}_duration'] / duration if duration else np.nan
            result[f'{instrument}_{label}_price_vol'] = adj
            adjusted.append(adj)
        a, b, c = adjusted
        result[f'corr_{label}'] = (a*a - b*b - c*c) / (2*b*c) if b*c else np.nan
    return pd.Series(result).replace([np.inf, -np.inf], np.nan)


def load_returns(start, end, read_sql):
    returns = []
    for sec, name in [('CDX_HY_OTR', 'CDX-NAHY'), ('CDX_IG_OTR', 'CDX-NAIG')]:
        raw = read_sql(f"SELECT asof_date, value FROM security_ts_hist WHERE sec_id = '{sec}' "
                       "AND attribute = 'TOTAL_RET_LEVEL' AND frequency = 'DAILY' AND source = 'JPM' "
                       f"AND asof_date BETWEEN '{start}' AND '{end}' ORDER BY asof_date", app='databricks')
        raw = raw.drop_duplicates('asof_date').set_index('asof_date').value.sort_index().pct_change(fill_method=None)
        raw.index = pd.to_datetime(raw.index)
        returns.append(raw.rename(name))
    raw = read_sql("SELECT asof_date, sec_id, value FROM security_ts_hist WHERE attribute = 'TOTAL_RET' "
                   "AND frequency = 'DAILY' AND sec_id ILIKE 'tsy_%' "
                   f"AND CAST(asof_date AS DATE) BETWEEN '{start}' AND '{end}'", app='databricks')
    treasury = raw.pivot_table(index='asof_date', columns='sec_id', values='value') / 10000
    treasury.index = pd.to_datetime(treasury.index)
    returns.append(treasury)
    return pd.concat(returns, axis=1).sort_index()


def realized(returns, days):
    rvol = returns.rolling(days, min_periods=2).std() * np.sqrt(252)

    ervol = returns.rolling(days, min_periods=2).apply(
        lambda x: np.sqrt(x.ewm(span=max(2, round(days/3)), adjust=False).var().iloc[-1]) * np.sqrt(252))
    return rvol, ervol


def correlation_view(frame):
    labels = {'corr_rvol': 'Correlation from ordinary realized vols',
              'corr_ervol': 'Correlation from EW realized vols'}
    return frame.filter(regex='^corr_').rename(columns=lambda col: labels.get(
        col, f"Implied correlation · {col.removeprefix('corr_')}d"))


def with_realized(out, returns, instruments, days, cache=None, rebuild=False):
    if cache:
        def compute(frame):
            rvol, ervol = realized(frame, days)
            return pd.concat([rvol.add_prefix('rvol:'), ervol.add_prefix('ervol:')], axis=1)
        stored = cache.rolling(f'realized:{days}', returns, days, compute, rebuild=rebuild)
        ervol = stored.filter(like='ervol:').rename(columns=lambda c: c.split(':', 1)[1])
        rvol = stored[[c for c in stored if c.startswith('rvol:')]].rename(columns=lambda c: c.split(':', 1)[1])
    else:
        rvol, ervol = realized(returns[[RETURN_IDS[i] for i in instruments]], days)
    result = out.copy()
    for name, vol in [('rvol', rvol), ('ervol', ervol)]:
        adjusted = []
        for instrument in instruments:
            raw = vol[RETURN_IDS[instrument]].reindex(result.index)
            result[f'{instrument}_{name}'] = raw
            adjusted.append(raw * result[f'{instruments[0]}_duration'] / result[f'{instrument}_duration'])
        a, b, c = adjusted
        result[f'corr_{name}'] = (a*a - b*b - c*c) / (2*b*c)
    return result.replace([np.inf, -np.inf], np.nan)


def run_model(params):
    from data_access.dbtables.market_data.credit.cdx_bbg_inputs import equity_spots, equity_returns
    from data_access.dbtables.market_data.credit import cds_index_vol
    from data_access.dbtables.market_data.vol import equity_index_vol_data, irswaption_vol_curve_data
    from common.utils.sqlutil import readdfsql
    from pricing_wrapper.market.curves.zerorate import get_fin_curves_by_ccy
    start, end = params['start'], params['end']
    from dash_reports.cdx_research.cache import DailyCache
    cache = DailyCache()
    rebuild = params.get('rebuild', False)
    yield {'title': 'Refresh scope', 'note': 'Rebuilding selected history.' if rebuild else
           'Using saved local AzureDB history. Fetching missing dates and the last five business days; recalculating results only when their inputs change.'}
    try:
        from dash_reports.cdx_research.return_correlations import run_weekly
        yield from run_weekly(cache, start, end, readdfsql, params)
    except Exception as exc:
        logging.exception('Weekly return correlations failed')
        yield {'title': 'Weekly return correlations', 'error': str(exc)}
    sources = []
    for title, loader, normalizer in [
        ('Equity and ETF vols', equity_index_vol_data.get_correlation_marks, normalize_equity),
        ('CDX vols', cds_index_vol.get_correlation_marks, normalize_credit),
        ('Swaption vols', irswaption_vol_curve_data.get_correlation_marks, None),
    ]:
        try:
            def load_source(lo, hi):
                raw = loader(lo, hi)
                if raw.empty:
                    return raw
                raw = raw[raw['tenor' if title == 'CDX vols' else 'option_tenor'].isin(TENOR_DAYS)]
                return normalizer(raw) if normalizer else normalize_rates(raw, get_fin_curves_by_ccy(lo, hi, 'USD'))
            frame = cache.load('source:' + title, start, end, load_source, rebuild=rebuild,
                               transform=lambda data: data[data.option_tenor.isin(params['tenors'])])
            if frame.empty:
                yield {'title': title, 'error': 'No source rows in the selected range.'}
                continue
            frame = frame[frame.option_tenor.isin(params['tenors'])]
            summary = frame.groupby(['bbg_id', 'option_tenor']).agg(
                observations=('asof_date', 'size'), first_date=('asof_date', 'min'), last_date=('asof_date', 'max')).reset_index()
            activity = cache.activity[-1]
            yield table_panel(title, summary, f"Saved history: {activity['reused_dates']} dates reused; {activity['refreshed_dates']} dates refreshed.")
            yield table_panel(title + ' — latest source data', frame[frame.asof_date == frame.asof_date.max()],
                              'All source fields at the latest observation date for the selected tenors. Historical calculated fields are available under All model fields.')
            latest = frame[frame.asof_date == frame.asof_date.max()]
            smile = latest.pivot_table(index='relative_strike', columns=['bbg_id', 'option_tenor'], values='price_vol')
            yield lines(f'{title} — latest delta smile', smile,
                        f'Annual price vol, decimal units; source date {frame.asof_date.max():%Y-%m-%d}.')
            columns = ['asof_date', 'bbg_id', 'option_tenor', 'relative_strike', 'price_vol',
                       'duration', 'spot_price', 'spot_spread', 'atmf_rate', 'be']
            sources.append(frame[[c for c in columns if c in frame]])
            del frame, latest, smile
        except Exception as exc:
            logging.exception('Vol source failed: %s', title)
            yield {'title': title, 'error': str(exc)}
    if not sources:
        raise ValueError('No volatility sources could be loaded')
    vols = pd.concat(sources, ignore_index=True)
    sources.clear()
    frame = None
    try:
        spots = equity_spots(start, end)
        vols = vols.merge(spots, on=['asof_date', 'bbg_id'], how='left', suffixes=('', '_bbg'))
        for col in ('spot_price', 'spot_spread', 'duration'):
            vols[col] = vols[f'{col}_bbg'].combine_first(vols[col])
        yield table_panel('ETF spot and duration data', spots.sort_values('asof_date').groupby('bbg_id').tail(1),
                          'Saved Bloomberg inputs from the local desktop loader. Source dates show the latest available observations.')
    except Exception as exc:
        logging.exception('Saved Bloomberg spot data failed')
        yield {'title': 'ETF spot and duration data', 'error': str(exc)}
    for col in ('duration', 'spot_price', 'spot_spread', 'atmf_rate', 'be'):
        if col not in vols:
            vols[col] = np.nan

    outputs = {}
    for tenor in params['tenors']:
        comparison = cache.calculate('spx-hy:' + tenor, vols[vols.option_tenor == tenor],
            lambda group: pd.Series({instrument: interpolate_delta(group[group.bbg_id == instrument], .5, 'price_vol')
                                     for instrument in ('SPX', 'CDX-NAHY')}), rebuild=rebuild)
        if not comparison.empty:
            yield lines(f'SPX / HY {tenor} — ATM price vols', comparison,
                        'Unadjusted annual decimal price vols at 50 delta.')
            yield lines(f'SPX / HY {tenor} — ATM vol ratio',
                        (comparison.SPX / comparison['CDX-NAHY'].replace(0, np.nan)).to_frame('SPX / CDX-NAHY'))
    for name, instruments in PAIRS.items():
        for tenor in params['tenors']:
            try:
                one = vols[vols.option_tenor == tenor]
                key = f"implied:{name}:{tenor}:" + ','.join(str(d) for d in params['deltas'])
                out = cache.calculate(key, one, lambda group: implied_corr(group, params['deltas'], instruments), rebuild=rebuild)
                outputs[(name, tenor)] = out
                yield lines(f'{name} {tenor} — duration-adjusted price vols',
                            out[[c for c in out if c.endswith('_price_vol') and '_unadj_' not in c]],
                            'Annual decimal price vols normalized to ETF duration.')
                yield lines(f'{name} {tenor} — implied correlation', correlation_view(out),
                            'Delta interpolation uses numeric distance with no extrapolation. Implied correlations are not clipped.')
                yield table_panel(f'{name} {tenor} — model data', out.reset_index())
            except Exception as exc:
                logging.exception('Model calculation failed: %s %s', name, tenor)
                yield {'title': f'{name} {tenor} — model', 'error': str(exc)}
    try:
        def fresh_returns(lo, hi):
            warmup = str((pd.Timestamp(lo) - pd.Timedelta(days=10)).date())
            frame = load_returns(warmup, hi, readdfsql)
            frame.index.name = 'asof_date'
            return frame.loc[lo:hi].reset_index()
        returns = cache.load('source:credit-and-treasury-returns', start, end, fresh_returns, rebuild=rebuild).set_index('asof_date').sort_index()
        saved_returns = equity_returns(start, end)
        saved_dates = ', '.join(f'{name}: {values.last_valid_index():%Y-%m-%d}' for name, values in saved_returns.items())
        returns = pd.concat([saved_returns, returns], axis=1).sort_index()
        swaps = vols[vols.bbg_id.str.startswith('USD')].copy()
        if not swaps.empty:
            swap_marks = swaps.assign(duration=swaps.duration.abs(), atmf_rate=swaps.atmf_rate.abs()).groupby(
                ['asof_date', 'option_tenor'])[['atmf_rate', 'duration']].mean().unstack()
            swap_returns = -swap_marks['atmf_rate'].diff() * swap_marks['duration'].shift(1)
            returns = returns.join(swap_returns.add_prefix('USD SOFR + '), how='outer').sort_index()
        yield lines('Daily input returns — all instruments', returns,
                    'Decimal daily total returns. Latest saved Bloomberg dates — ' + saved_dates + '.')
        from itertools import combinations
        window = params['window']
        all_corr = cache.rolling(f'all-pair-correlations:{window}', returns, window,
            lambda data: pd.DataFrame({f'{a} / {b}': data[a].rolling(window).corr(data[b])
                                      for a, b in combinations(data.columns, 2)}), rebuild=rebuild)
        yield lines(f'All pairs — daily return correlations ({window} observations)', all_corr,
                    'Choose multiple instrument pairs. Each estimate requires a complete window of matched returns.',
                    selected=[c for c in all_corr if c in ('CDX-NAHY / tsy_5y', 'CDX-NAIG / tsy_10y',
                                                          'CDX-NAHY / USD SOFR + 1m', 'CDX-NAIG / USD SOFR + 1m')])
    except Exception as exc:
        yield {'title': 'Realized returns', 'error': str(exc)}
        return
    for name, instruments in PAIRS.items():
        etf, credit, rate = [RETURN_IDS[i] for i in instruments]
        try:
            aligned = returns[[etf, credit, rate]].dropna()
            window = params['window']
            corr = cache.rolling(f'correlation:{name}:{window}', aligned, window,
                lambda data: pd.DataFrame({f'{credit} / {rate}': data[credit].rolling(window).corr(data[rate]),
                                          f'{credit} / {etf}': data[credit].rolling(window).corr(data[etf])}), rebuild=rebuild)
            yield lines(f'{name} — realized correlation ({window} observations)', corr,
                        'Matched return dates only. Missing returns are not replaced with zero.')
            import statsmodels.api as sm
            fit = sm.OLS(aligned[etf], sm.add_constant(aligned[[credit, rate]])).fit()
            yield table_panel(f'{name} — ETF return regression', pd.DataFrame({
                'coefficient': fit.params, 'standard_error': fit.bse, 'p_value': fit.pvalues}).rename_axis('factor').reset_index(),
                f'{len(aligned):,} matched dates; R² {fit.rsquared:.3f}.')
            for tenor in params['tenors']:
                if (name, tenor) in outputs:
                    out = with_realized(outputs[(name, tenor)], returns, instruments, TENOR_DAYS[tenor], cache, rebuild)
                    cache.calculate(f'combined:{name}:{tenor}:' + ','.join(str(d) for d in params['deltas']),
                                    out.reset_index(), lambda group: group, rebuild=rebuild)
                    yield lines(f'{name} {tenor} — implied and realized vol correlation', correlation_view(out),
                                'Correlations inferred from ETF, credit and rate volatilities. Direct ordinary/EW return correlations appear in the Return correlations section.')
                    yield lines(f'{name} {tenor} — implied and realized price vols',
                                out[[c for c in out if '_unadj_price_vol' in c or c.endswith(('_rvol', '_ervol'))]],
                                'Annual decimal price vols; EW estimator follows the notebook span convention.')
                    yield lines(f'{name} {tenor} — all model fields', out,
                                'Select any combination of fields. Data tab includes the same selected series; units vary by field.',
                                selected=[c for c in out if c.startswith('corr_')])
        except Exception as exc:
            logging.exception('Realized calculation failed: %s', name)
            yield {'title': f'{name} — realized analytics', 'error': str(exc)}
    yield table_panel('Saved calculations', pd.DataFrame(cache.activity),
                      f'Local AzureDB table: {cache.path.name}. Dates with unchanged inputs reuse their saved results.')
