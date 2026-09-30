import glob
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

_UNIVERSES_DIR = os.path.join(os.path.dirname(__file__), 'universes')


@dataclass(frozen=True)
class Ticker:
    ticker_region: str
    name: str


@dataclass(frozen=True)
class Industry:
    key: str
    display_name: str
    subtitle: str
    sub_groups: List[Tuple[str, List[Ticker]]] = field(default_factory=list)
    credit_sub_groups: List[Tuple[str, List[Ticker]]] = field(default_factory=list)

    def all_tickers(self) -> List[Tuple[str, str, str]]:
        out = []
        for sg_name, tickers in self.sub_groups:
            for t in tickers:
                out.append((sg_name, t.ticker_region, t.name))
        return out

    def sub_group_names(self) -> List[str]:
        return [sg for sg, _ in self.sub_groups]

    def credit_tickers(self) -> List[str]:
        seen, out = set(), []
        for _, tickers in self.credit_sub_groups:
            for t in tickers:
                tk = t.ticker_region
                if tk and tk != '???' and tk not in seen:
                    seen.add(tk)
                    out.append(tk)
        return out

    def credit_sub_group_for_ticker(self) -> Dict[str, str]:
        out = {}
        for sg_name, tickers in self.credit_sub_groups:
            for t in tickers:
                if t.ticker_region and t.ticker_region != '???' and t.ticker_region not in out:
                    out[t.ticker_region] = sg_name
        return out

    def credit_sub_group_names(self) -> List[str]:
        return [sg for sg, _ in self.credit_sub_groups]


def _load_sub_groups(payload_section: list) -> List[Tuple[str, List[Ticker]]]:
    out = []
    for entry in payload_section or []:
        sg = entry.get('subsector') or entry.get('sub_group') or entry.get('name') or ''
        tickers = [Ticker(t['ticker'], t.get('name') or t['ticker'])
                   for t in entry.get('tickers') or []]
        if sg:
            out.append((sg, tickers))
    return out


def _load_sector_groups(payload: dict, section_name: str) -> List[Tuple[str, List[Ticker]]]:
    out = []
    for sector in payload.get('sectors') or []:
        sector_name = sector.get('sector') or sector.get('name') or ''
        for entry in sector.get(section_name) or []:
            sub = entry.get('subsector') or entry.get('sub_group') or entry.get('name') or ''
            if not sector_name or not sub:
                continue
            tickers = [Ticker(t['ticker'], t.get('name') or t['ticker'])
                       for t in entry.get('tickers') or []]
            out.append((f'{sector_name} / {sub}', tickers))
    return out


def _load_industry_from_json(path: str) -> Optional[Industry]:
    with open(path, 'r', encoding='utf-8') as f:
        payload = json.load(f)
    key = payload.get('key') or os.path.splitext(os.path.basename(path))[0]
    has_sectors = bool(payload.get('sectors'))
    ind = Industry(
        key=key,
        display_name=payload.get('display_name') or key.title(),
        subtitle=payload.get('subtitle') or '',
        sub_groups=_load_sector_groups(payload, 'public_comps') if has_sectors
        else _load_sub_groups(payload.get('public_comps')),
        credit_sub_groups=_load_sector_groups(payload, 'credit_comps') if has_sectors
        else _load_sub_groups(payload.get('credit_comps')),
    )
    _validate_unique_tickers(ind, path)
    return ind


def _validate_unique_tickers(industry: Industry, path: str):
    for label, groups in [('public_comps', industry.sub_groups),
                          ('credit_comps', industry.credit_sub_groups)]:
        seen = {}
        duplicates = []
        for sg_name, tickers in groups:
            for t in tickers:
                ticker = t.ticker_region
                if not ticker or ticker == '???':
                    continue
                if ticker in seen:
                    duplicates.append(f'{ticker}: {seen[ticker]} / {sg_name}')
                else:
                    seen[ticker] = sg_name
        if duplicates:
            raise ValueError(f'duplicate {label} tickers in {path}: {duplicates}')


def _load_all_industries() -> Dict[str, Industry]:
    out: Dict[str, Industry] = {}
    if not os.path.isdir(_UNIVERSES_DIR):
        logging.warning('universes dir missing: %s', _UNIVERSES_DIR)
        return out
    for path in sorted(glob.glob(os.path.join(_UNIVERSES_DIR, '*.json'))):
        try:
            ind = _load_industry_from_json(path)
            if ind:
                out[ind.key] = ind
        except Exception:
            logging.exception('failed to load universe from %s', path)
    return out


INDUSTRY_UNIVERSE: Dict[str, Industry] = _load_all_industries()


def get_industry(key: str) -> Optional[Industry]:
    return INDUSTRY_UNIVERSE.get(key)


def list_industries() -> List[Industry]:
    return list(INDUSTRY_UNIVERSE.values())


def reload_universes() -> Dict[str, Industry]:
    global INDUSTRY_UNIVERSE
    INDUSTRY_UNIVERSE = _load_all_industries()
    return INDUSTRY_UNIVERSE
