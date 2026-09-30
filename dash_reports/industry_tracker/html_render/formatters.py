import html
from typing import Optional

import pandas as pd


CURRENCY_SYMBOL = {
    'USD': '$', 'EUR': '€', 'GBP': '£', 'JPY': '¥', 'HKD': 'HK$',
    'CNY': '¥', 'KRW': '₩', 'CHF': 'CHF ', 'SEK': 'kr ', 'DKK': 'kr ',
    'NOK': 'kr ', 'CAD': 'C$', 'AUD': 'A$', 'BRL': 'R$', 'MXN': 'Mex$',
    'INR': '₹', 'PLN': 'zł ', 'TRY': '₺', 'RUB': '₽', 'ZAR': 'R ',
    'SGD': 'S$', 'NZD': 'NZ$', 'TWD': 'NT$', 'THB': '฿', 'IDR': 'Rp ',
}


def esc(value) -> str:
    if value is None or _isna(value):
        return ''
    return html.escape(str(value), quote=True)


def _isna(value) -> bool:
    try:
        return pd.isna(value)
    except (TypeError, ValueError):
        return False


def fmt_price(value, currency: Optional[str]) -> str:
    if value is None or _isna(value):
        return '--'
    sym = CURRENCY_SYMBOL.get((currency or '').upper(), '')
    return f'{sym}{value:,.2f}'


def fmt_pct(value, signed: bool = False, decimals: int = 1) -> str:
    if value is None or _isna(value):
        return '--'
    s = f'{value:+.{decimals}f}%' if signed else f'{value:.{decimals}f}%'
    return s


def fmt_pct_no_sign(value, decimals: int = 1) -> str:
    if value is None or _isna(value):
        return '--'
    return f'{value:.{decimals}f}%'


def fmt_x(value, decimals: int = 1, signed: bool = False) -> str:
    if value is None or _isna(value):
        return '--'
    s = f'{value:+.{decimals}f}x' if signed else f'{value:.{decimals}f}x'
    return s


def fmt_money_billions(value_in_millions) -> str:
    if value_in_millions is None or _isna(value_in_millions):
        return '--'
    return f'{value_in_millions / 1000.0:,.1f}'


def fmt_int(value) -> str:
    if value is None or _isna(value):
        return '--'
    return f'{int(value):,}'


def cls_pos_neg(value) -> str:
    if value is None or _isna(value):
        return ''
    return 'pos' if value >= 0 else 'neg'


_SLUG_RE = __import__('re').compile(r'[^a-z0-9]+')


def slug(text: str) -> str:
    s = (text or '').lower().replace('&', ' and ')
    s = _SLUG_RE.sub('-', s)
    return s.strip('-')
