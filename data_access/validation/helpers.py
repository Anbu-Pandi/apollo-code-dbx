import pandas as pd
from dateutil import relativedelta

VALIDATERESULTSCHEMA = 'column string, validatetype string, group string, description string, failedcount long, totalcount long'

# None is used with validate type and default params will be used
price_tsdict = dict(
    missing=None,
    stale=None,
    percentile=None,
    zscore=None,
    range_lo=0,
    range_hi=200,
    percent_threshold=0.10
)

return_tsdict = dict(
    missing=None,
    stale=None,
    percentile=None,
    zscore=None,
    range_lo=-0.5,
    range_hi=.5,
    change_threshold=.10
)

rate_tsdict = dict(
    missing=None,
    stale=None,
    percentile=None,
    zscore=None,
    range_lo=-1,
    range_hi=5,
    change_threshold=0.10
)

ratepct_tsdict = dict(
    missing=None,
    stale=None,
    percentile=None,
    zscore=None,
    range_lo=-100,
    range_hi=500,
    change_threshold=10
)

ratebps_tsdict = dict(
    stale=None,
    percentile=None,
    zscore=None,
    range_lo=-10000,
    range_hi=50000,
    change_threshold=1000
)

duration_tsdict = dict(
    missing=None,
    stale=None,
    percentile=None,
    zscore=None,
)

notional_tsdict = dict(
    missing=None,
    stale=None,
    percentile=None,
    zscore=None,
    percent_threshold=0.10
)

volume_tsdict = dict(
    missing=None,
    stale=None,
    percentile=None,
    zscore=None
)

qty_tsdict = dict(
    missing=None,
    stale=None,
    percentile=None,
    zscore_threshold=5
)

weights_tsdict = dict(qty_tsdict, **dict(change_threshold=0.01))

coupon_tsdict = dict(change_treshold=.001)


def _dateadd(dt, **kwargs):
    retdt = None
    if dt is not None:
        retdt = (pd.to_datetime(dt) + relativedelta.relativedelta(**kwargs)).strftime('%Y-%m-%d')
    return retdt
