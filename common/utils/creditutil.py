import logging
import re

import numpy as np
import pandas as pd

MATURITY_BUCKET = [-99, 0, 4, 7.5, 12, 20, 9999]
RATING_BUCKET = ['AAA', 'AA', 'A', 'BBB', 'BB', 'B', 'CCC or lower', 'D', 'NR']

_rating_last_char_adj = {'3': 3, '2': 2, '1': 1, '+': 1, '-': 3}
rating_mapper = lambda x: int((ord(x[:1].upper()) - 64) * 1e6 \
                              + (10 - len(re.sub('[^a-zA-Z]', '', x)) * 1e3 \
                                 + _rating_last_char_adj.get(x[-1], 0)))


def _process_rating(rating_column):
    processed = rating_column.fillna('NR')
    processed = processed.apply(lambda x: _handle_special_rating_cases(x) if isinstance(x, str) else x)
    return processed.str.replace('[^a-zA-Z]', '').replace(r'^\s*$', 'NR', regex=True)


def _handle_special_rating_cases(rating):
    if not isinstance(rating, str):
        return 'NR'

    upper_rating = rating.upper()

    if any(nr_term in upper_rating for nr_term in ['NOT RATED', 'UNRATED', 'NO RATING']):
        return 'NR'

    if 'SPLIT' in upper_rating:
        for grade in ['AAA', 'AA', 'A', 'BBB', 'BB', 'B', 'CCC', 'CC', 'C', 'D']:
            if grade in upper_rating:
                return grade

    if any(c_rating in upper_rating.split('/') for c_rating in ['CCC', 'CC', 'C']):
        return 'CCC'

    return rating


def map_rating(rating_column):
    col = _map_rating_int(rating_column)
    mapped_rating = pd.Series(list(map(rating_mapper, RATING_BUCKET))).to_frame('rating_mapped')
    mapped_rating['out_rating'] = RATING_BUCKET
    mapped_rating = mapped_rating.reset_index(drop=True)

    out = col[['rating_mapped', 'in_rating_processed']].drop_duplicates().sort_values('rating_mapped')
    out = out.reset_index(drop=True)

    out = pd.merge_asof(out, mapped_rating, on='rating_mapped')

    col = col.reset_index()
    merged = pd.merge(
        col,
        out[['out_rating', 'rating_mapped']].drop_duplicates(),
        on='rating_mapped',
        how='left'
    )

    return merged.set_index('index')['out_rating']


def _map_rating_int(rating_column):
    col = rating_column.to_frame('in_rating')
    col['in_rating_processed'] = _process_rating(col['in_rating'])
    col['rating_mapped'] = col['in_rating_processed'].map(rating_mapper)
    return col


def map_rating_int(rating_column):
    return _map_rating_int(rating_column)['rating_mapped']


def _format_mat_string(x):
    return f'{x:05.1f}'


def map_maturity_bucket(maturity_column):
    maturity_column = maturity_column.fillna(0).clip(lower=0)
    col = pd.cut(maturity_column, MATURITY_BUCKET)
    col = col.map(lambda x: f'{_format_mat_string(x.left)}-{_format_mat_string(x.right)}').astype(str)
    return col


def map_maturity_bucket_int(maturity_column, bucket_type='lb'):
    maturity_column = maturity_column.fillna(0).clip(lower=0)
    col = pd.cut(maturity_column, MATURITY_BUCKET)
    if bucket_type == 'lb':
        col = col.map(lambda x: x.left)
    elif bucket_type == 'lb':
        col = col.map(lambda x: x.right)
    else:
        col = col.map(lambda x: 0.5 * max(0, x.left) + 0.5 * min(MATURITY_BUCKET[-2], x.right))
    return col


def calc_mid_maturity_bucket(mty_bucket):
    if pd.isnull(mty_bucket):
        return '1y'
    lb = _format_mat_string(MATURITY_BUCKET[0])
    ub = _format_mat_string(MATURITY_BUCKET[-1])
    if lb == mty_bucket[:len(lb)]:
        return '1y'
    xa = mty_bucket.split('-')
    if ub == mty_bucket[-len(ub):]:
        return str(int(float(xa[0]) * 12)) + 'm'
    return str(int(12 * np.mean([float(xa[0]), float(xa[1])]))) + 'm'


def get_mid_maturity_buckets():
    return [calc_mid_maturity_bucket(f'{_format_mat_string(l)}-{_format_mat_string(u)}')
            for l, u in zip(MATURITY_BUCKET[:-1], MATURITY_BUCKET[1:])]


def calc_tenor(mty, refdt):
    if pd.isnull(mty) or pd.isnull(refdt):
        return np.nan
    return np.array([(mty - refdt)], dtype="timedelta64[D]")[0].astype(np.int32) / 365.25


vec_calc_tenor = np.vectorize(calc_tenor)


def map_secured_unsecured(ds, is_perp_ds=None):
    def _update_ds(_ds, flags, value):
        _i = _ds[flags].index.tolist()
        _u_ds = pd.Series([value] * len(_i), index=_i)
        _ds.update(_u_ds)

    us_ds = ds.copy()
    _update_ds(us_ds, [True] * len(us_ds), '')

    bbg_secureds = ['1ST MORTGAGE', '1ST REF MORT', '2ND MORTGAGE', 'ASSET BACKED', 'COLLATERALIZED', 'GENL REF MORT',
                    'MORTGAGE', 'MORTGAGE BACKED', 'SECURED', 'SR SECURED']
    bbg_subs = ['JR SUB DEBS', 'JR SUBORDINATED', 'SR SUB DEBS', 'SR SUB NOTES', 'SR SUBORDINATED', 'SUB DEBENTURES',
                'SUB NOTES', 'SUBORDINATED']

    _update_ds(us_ds, ds.isin(bbg_secureds), 'SEC')
    _update_ds(us_ds, ds.isin(bbg_subs), 'SUB')

    sec_flag = (us_ds == '') & ds.str.startswith('SECR')
    unsec_flag = (us_ds == '') & ds.str.contains('SEN')

    _update_ds(us_ds, sec_flag, 'SEC')
    _update_ds(us_ds, unsec_flag, 'UNSEC')
    _update_ds(us_ds, (us_ds == ''), 'UNSEC')

    if not is_perp_ds is None:
        _update_ds(us_ds, (is_perp_ds.fillna(0) == 1), 'SUB')

    return us_ds


if __name__ == '__main__':
    from common.utils.logutil import setuplogger
    from common.utils import dtutil

    setuplogger()

    dt1 = dtutil.todate('2022-01-01')
    dt2 = dtutil.todate('2022-09-25')

    logging.info(calc_tenor(dt2, dt1))

    logging.info(get_mid_maturity_buckets())

    rating_mapper('NR')

    maturities = pd.DataFrame(range(50))
    buckets = map_maturity_bucket_int(maturities[0])
    logging.info(buckets)

    rating = pd.DataFrame(['A1', 'A2', 'AAA', 'AA1', 'AA2', 'AA3', 'BB', 'BBB', 'BBB1', 'BBB+', 'BBB2', 'BBB3', 'BBB-',
                           np.nan, 'BB1', 'BB2', 'B1', 'B2', 'C', 'CCC', 'D', 'NR', ''], columns=['rating'])

    rating['rating_mapped_int'] = map_rating_int(rating['rating'])
    rating['rating_mapped'] = map_rating(rating['rating'])

    logging.info(rating)

    logging.info(rating_mapper('BBB+'))
