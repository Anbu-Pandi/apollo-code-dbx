"""Return correlations, including the weekly estimator in notebook cell 120."""
import numpy as np
import pandas as pd


def weekly_returns(start, end, read_sql):
    warmup = str((pd.Timestamp(start) - pd.Timedelta(days=21)).date())
    raw = read_sql("SELECT asof_date, sec_id, attribute, value FROM security_ts_hist "
                   "WHERE sec_id IN ('CDX_HY_OTR', 'CDX_IG_OTR', 'tsy_10y') "
                   "AND attribute IN ('TOTAL_RET', 'TOTAL_RET_LEVEL') "
                   "AND frequency = 'WEEKLY' AND source = 'JPM' "
                   f"AND asof_date BETWEEN '{warmup}' AND '{end}'", app='databricks')
    if raw.empty:
        return raw
    raw['asof_date'] = pd.to_datetime(raw.asof_date)
    series = []
    for name in ('CDX_HY_OTR', 'CDX_IG_OTR', 'tsy_10y'):
        attribute = 'TOTAL_RET' if name == 'tsy_10y' else 'TOTAL_RET_LEVEL'
        part = raw[(raw.sec_id == name) & (raw.attribute == attribute)]
        value = part.groupby('asof_date').value.last().sort_index()
        value = value / 10000 if name == 'tsy_10y' else value.pct_change(fill_method=None)
        series.append(value.rename(name))
    result = pd.concat(series, axis=1).sort_index().loc[start:end]
    return result.rename_axis('asof_date').reset_index()


def notebook_correlations(frame, credit, rate='tsy_10y', window=12, half_life=4):
    x, y = frame[credit], frame[rate]
    ordinary = x.rolling(window).corr(y)

    covariance = x.rolling(window).apply(
        lambda sample: sample.ewm(halflife=half_life).mean().cov(y.loc[sample.index]), raw=False)
    ew = covariance / np.sqrt(x.ewm(halflife=half_life).var() * y.ewm(halflife=half_life).var())
    return pd.DataFrame({'Ordinary return correlation': ordinary,
                         'EW return correlation (notebook)': ew})


def run_weekly(cache, start, end, read_sql, params):
    from dash_reports.cdx_research.pipeline import lines
    rebuild = params.get('rebuild', False)
    frame = cache.load('source:weekly returns', start, end,
                       lambda lo, hi: weekly_returns(lo, hi, read_sql), rebuild=rebuild)
    if frame.empty:
        yield {'title': 'Weekly return correlations', 'error': 'No weekly source returns in this date range.'}
        return
    frame = frame.set_index('asof_date').sort_index()
    window, half_life = params['weekly_window'], params['half_life']
    for name, credit in [('HY', 'CDX_HY_OTR'), ('IG', 'CDX_IG_OTR')]:
        matched = frame[[credit, 'tsy_10y']].dropna()

        output = cache.rolling(f'weekly:{name}:{window}:{half_life}', matched, max(1, len(matched)),
                               lambda data: notebook_correlations(data, credit, window=window, half_life=half_life),
                               rebuild=rebuild)
        yield lines(f'{name} — weekly ordinary and EW return correlations', output,
                    f'{window}-week window; {half_life:g}-week half-life. Notebook EW formula: covariance of EW-smoothed credit returns with Treasury returns, divided by EW standard deviations. '
                    'Credit levels are converted to returns for both HY and IG; exact matching dates only. '
                    f'{len(matched)} matched observations available; {window} required for the first correlation.')
        yield lines(f'{name} — weekly input returns', matched, 'Decimal weekly returns; 10-year Treasury for both pairs, as in the notebook.')
