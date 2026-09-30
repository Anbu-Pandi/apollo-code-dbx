"""
Diagnostic script: run the CDX options pipeline against prod data and test every tab.
Usage: python -m dash_reports.cdx_options.data.diag_pipeline --env prod
"""
import logging
import sys
from datetime import datetime

import numpy as np
import pandas as pd
import pytz

from common.utils.envsetup import init_app_using_parser, get_base_parser
parser = get_base_parser('diag_pipeline', log_file='beta_attribution_dash_report')
parser.set_defaults(env='prod')
init_app_using_parser(argparser=parser)

from credit_strategy.cdx_bbg_msg.cdx_option_quote_agg import CDXOptionQuoteAgg
from credit_strategy.cdx_bbg_msg.vol_mark import VolMark
from dash_reports.cdx_options.data.data_loader import CDXOptionsDataLoader, MARK_ID
from dash_reports.cdx_options.data.pricing_grid import PricingGrid, is_price_based
from dash_reports.cdx_options.data.charts import build_option_ladder
from dash_reports.cdx_options.data.trade_blotter import build_trade_blotter_data
from dash_reports.cdx_options.data.positions import build_position_greeks_data

NY = pytz.timezone('America/New_York')

PASS = 'PASS'
FAIL = 'FAIL'
WARN = 'WARN'


def banner(txt):
    logging.info('')
    logging.info('=' * 70)
    logging.info(f'  {txt}')
    logging.info('=' * 70)


def check(name, ok, detail=''):
    status = PASS if ok else FAIL
    logging.info(f'  [{status}] {name}{f"  --  {detail}" if detail else ""}')
    return ok


def run_diag():
    mark_time = '15:00'
    tickers = ['CDX-NAHY', 'CDX-NAIG']
    todt = datetime.now(NY).date()

    loader = CDXOptionsDataLoader(tickers=tickers, todt=todt, lookback='6m')
    loader.mark_time_override = mark_time

    banner(f'FULL LOAD: tickers={tickers}, todt={todt}, mark_time={mark_time}')
    loader.load_all_with_progress()

    products = loader.get_available_products()
    logging.info(f'Products: {products}')

    results = {}
    plot_data = None

    banner('TAB 1: VOL GRIDS  (needs curr_mark + trades + grids)')
    ok = True
    ok &= check('curr_mark non-empty', not loader.curr_mark.empty, f'{len(loader.curr_mark)} rows')
    ok &= check('cdx_opt_trades non-empty', not loader.cdx_opt_trades.empty, f'{len(loader.cdx_opt_trades)} rows')
    ok &= check('pricing_grids loaded', bool(loader.pricing_grids), f'{len(loader.pricing_grids)} grids')

    for product in products:
        sub = loader.curr_mark[loader.curr_mark['product'] == product]
        if sub.empty:
            check(f'{product}: curr_mark', False, 'no rows')
            ok = False
            continue
        check(f'{product}: curr_mark', True, f'{len(sub)} rows')
        key_cols = ['implied_vol', 'payer_premium', 'receiver_premium', 'delta', 'vega_ann']
        for kc in key_cols:
            n = sub[kc].notna().sum() if kc in sub.columns else 0
            check(f'{product}: {kc}', n > 0, f'{n}/{len(sub)} filled')

        expiries = loader.get_available_expiries(product)
        strikes = loader.get_available_strikes(product)
        if not expiries or not strikes:
            check(f'{product}: build_plot_dfs', False, f'expiries={len(expiries)}, strikes={len(strikes)}')
            ok = False
            continue

        exp0, strike0 = expiries[0], strikes[0]
        try:
            plot_data = loader.build_plot_dfs(product, strike0, exp0)
            if plot_data is None:
                check(f'{product}: build_plot_dfs({strike0}, {exp0})', False, 'returned None')
                ok = False
            else:
                has_vols = not plot_data['vols'].empty
                has_prem = not plot_data['payer_prem'].empty
                check(f'{product}: build_plot_dfs', has_vols and has_prem,
                      f'vols={plot_data["vols"].shape}, payer_prem={plot_data["payer_prem"].shape}, '
                      f'trades_intraday={len(plot_data["intraday_trades_df"])}')
                if not has_vols:
                    ok = False
        except Exception as e:
            check(f'{product}: build_plot_dfs EXCEPTION', False, str(e)[:200])
            ok = False
    results['vol_grids'] = ok

    banner('TAB 2: TRADE BLOTTER  (needs cdx_opt_trades, optionally curr_mark)')
    ok = True
    ok &= check('cdx_opt_trades non-empty', not loader.cdx_opt_trades.empty, f'{len(loader.cdx_opt_trades)} rows')
    early = loader.build_early_trades()
    ok &= check('build_early_trades', bool(early), f'{len(early)} keys')

    if plot_data:
        blotter = build_trade_blotter_data(plot_data)
        ok &= check('build_trade_blotter_data', bool(blotter),
                     f'{len(blotter)} sections' if blotter else 'empty')
        for label, section in (blotter or {}).items():
            n = len(section.get('data', []))
            check(f'  blotter[{label}]', n > 0, f'{n} trades')
    results['trade_blotter'] = ok

    banner('TAB 3: POSITIONS & GREEKS  (needs cdx_opt_pos_df + grids)')
    ok = True
    ok &= check('cdx_opt_pos_df non-empty', not loader.cdx_opt_pos_df.empty, f'{len(loader.cdx_opt_pos_df)} rows')

    for product in products:
        try:
            posdf = loader._build_posdf(product)
            if posdf.empty:
                check(f'{product}: _build_posdf', False, 'empty')
                continue
            check(f'{product}: _build_posdf', True, f'{len(posdf)} rows')
            greeks = build_position_greeks_data(posdf, product)
            check(f'{product}: build_position_greeks_data', bool(greeks),
                  f'{len(greeks)} keys' if greeks else 'empty')
            if greeks:
                for k, v in greeks.items():
                    if hasattr(v, 'shape'):
                        check(f'  greeks[{k}]', v.shape[0] > 0, f'{v.shape}')
        except Exception as e:
            check(f'{product}: positions EXCEPTION', False, str(e)[:200])
            ok = False
    results['positions'] = ok

    banner('TAB 4: EXPIRY VOL CHARTS  (needs curr_mark + sender_mark + vol_mark_df)')
    ok = True
    ok &= check('curr_mark non-empty', not loader.curr_mark.empty, f'{len(loader.curr_mark)} rows')
    ok &= check('sender_mark non-empty', not loader.sender_mark.empty, f'{len(loader.sender_mark)} rows')
    ok &= check('vol_mark_df non-empty', not loader.vol_mark_df.empty, f'{len(loader.vol_mark_df)} rows')

    for product in products:
        sub_curr = loader.curr_mark[loader.curr_mark['product'] == product]
        sub_sender = loader.sender_mark[loader.sender_mark['product'] == product]
        check(f'{product}: curr_mark', len(sub_curr) > 0, f'{len(sub_curr)} rows')
        check(f'{product}: sender_mark', len(sub_sender) > 0, f'{len(sub_sender)} rows')
        if sub_sender.empty:
            ok = False
        else:
            banks = sub_sender['sender_bank'].nunique() if 'sender_bank' in sub_sender.columns else 0
            check(f'{product}: sender banks', banks > 0, f'{banks} banks')
    results['expiry_charts'] = ok

    banner('TAB 5: OPTION STACK  (needs product_aggs with vol_calc cols)')
    ok = True
    ok &= check('product_aggs populated', bool(loader.product_aggs), f'{len(loader.product_aggs)} products')

    for product in products:
        agg = loader.product_aggs.get(product)
        if not agg:
            check(f'{product}: product_agg', False, 'missing')
            ok = False
            continue
        nq = len(agg.quotes)
        vol_cols = ['pay_vol_bid_calc', 'pay_vol_ask_calc', 'rec_vol_bid_calc', 'rec_vol_ask_calc']
        fills = {c: agg.quotes[c].notna().sum() for c in vol_cols if c in agg.quotes.columns}
        check(f'{product}: agg.quotes', nq > 0, f'{nq} quotes, vol_fills={fills}')

        expiry = agg.quotes['expiry'].dropna().unique()
        if len(expiry):
            try:
                ladder = build_option_ladder(agg, product, expiry=expiry[0])
                check(f'{product}: option_ladder(exp={expiry[0]})', not ladder.empty,
                      f'{len(ladder)} rows' if not ladder.empty else 'empty')
                if ladder.empty:
                    ok = False
            except Exception as e:
                check(f'{product}: option_ladder EXCEPTION', False, str(e)[:200])
                ok = False
    results['option_stack'] = ok

    banner('TAB 6: RELATIVE VOL  (needs vol_mark_df)')
    ok = True
    ok &= check('vol_mark_df non-empty', not loader.vol_mark_df.empty, f'{len(loader.vol_mark_df)} rows')
    if not loader.vol_mark_df.empty:
        vm_products = loader.vol_mark_df['product'].unique() if 'product' in loader.vol_mark_df.columns else []
        check('vol_mark products', len(vm_products) > 0, f'{sorted(vm_products)[:5]}')
        dates = loader.vol_mark_df['asof_date'].nunique() if 'asof_date' in loader.vol_mark_df.columns else 0
        check('vol_mark date range', dates > 5, f'{dates} unique dates')
    results['relative_vol'] = ok

    banner('SUMMARY')
    all_pass = True
    for tab, passed in results.items():
        status = PASS if passed else FAIL
        logging.info(f'  [{status}] {tab}')
        if not passed:
            all_pass = False

    logging.info('')
    if all_pass:
        logging.info('ALL TABS PASS -- pipeline is healthy')
    else:
        failed = [t for t, ok in results.items() if not ok]
        logging.info(f'FAILED TABS: {failed} -- needs attention')

    logging.info('DONE')
    return all_pass


if __name__ == '__main__':
    run_diag()
