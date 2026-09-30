import json
import os
import time
from urllib.parse import parse_qs, urlsplit

import pandas as pd
from plotly.utils import PlotlyJSONEncoder

from dash_reports.vc_dashboard.data import queries
from dash_reports.vc_dashboard.pages import callbacks, formatting, vc_dashboard


def test_activity_perspectives_share_filters_and_exact_participants(monkeypatch):
    today = pd.Timestamp.now().normalize()
    deals = pd.DataFrame({
        'deal_id': [str(n) for n in range(9)],
        'company': ['A'] * 7 + ['B', 'A'], 'deal_date': [today] * 8 + [today - pd.Timedelta(days=40)],
        'investors': [f'Alpha, Other {n}' for n in range(7)] + ['Alpha Growth', 'Alpha'],
        'lead_investors': ['Alpha'] * 7 + ['Alpha Growth', 'Alpha'],
        'deal_status': ['Completed'] * 6 + ['Announced', 'Completed', 'Completed'],
        'deal_size_usd': [10e6] * 6 + [99e6, 200e6, 1e6],
        'sector': ['AI'] * 9, 'region': ['Paris'] * 9, 'post_valuation_usd': [None] * 9,
    })
    monkeypatch.setattr(queries, '_deals_df', lambda env: deals)
    monkeypatch.setattr(queries, '_watchlist_names', lambda env: ['Alpha'])
    firms = queries.get_activity(window_days=30)
    assert len(firms) == 1 and firms[0]['deals'] == 7
    assert firms[0]['previous_deals'] == 1 and firms[0]['change'] == 6
    assert firms[0]['value_usd'] == 60e6
    assert firms[0]['counterparty_count'] == 1
    companies = queries.get_activity('company', 30, search='AI Paris')
    assert companies[0]['name'] == 'A' and len(companies[0]['counterparties']) == 5
    assert companies[0]['counterparties'][0]['name'] == 'Alpha'
    assert companies[0]['counterparties'][0]['deals'] == 7
    assert queries.get_activity('company', 30, min_size_usd=100e6)[0]['name'] == 'B'
    assert queries.get_activity('investor', 30, search='Alpha Growth') == []
    assert queries.get_activity('company', 30, search='missing') == []
    assert queries.get_activity('company', 30, sort='value')[0]['name'] == 'B'
    assert len(queries.get_deals(30, search='A', min_size_usd=100e6)) == 1
    assert queries.get_summary(30, search='Other', min_size_usd=20e6)['total_raised_usd'] == 0
    json.dumps(formatting.activity(companies), cls=PlotlyJSONEncoder, allow_nan=False)


def test_entity_links_route_to_generic_wiki_and_legacy_tabs_still_work():
    link = formatting.entity_link('company', 'A & B / Health?')
    assert parse_qs(urlsplit(link.href).query) == {
        'tab': ['news'], 'news_page': ['default/page/companies/A_B_Health']}
    assert parse_qs(urlsplit(formatting.entity_link('deal', '123-45T').href).query)['news_page'] == ['default/page/deals/123-45T']
    assert callbacks.toggle_panels('portfolio')[-1] == {'display': 'block'}
    assert callbacks.toggle_panels('relationships')[-1] == {'display': 'block'}
    _, layout = vc_dashboard.create_vc_dashboard_layout('relationships', selection={'q': 'OpenAI', 'window': '30'})
    tabs = next(child for child in layout.children if getattr(child, 'id', None) == 'vc-tabs')
    assert tabs.value == 'relationships'
    assert [tab.label for tab in tabs.children][2:4] == ['Company activity', 'Investor activity']


def test_activity_callback_passes_same_selection_for_both_tabs(monkeypatch):
    calls = []
    monkeypatch.setattr(callbacks.client, 'get_activity', lambda **kwargs: calls.append(kwargs) or [])
    callbacks.render_activity('relationships', 30, ['apollo'], 'AI', 25e6, 'momentum')
    callbacks.render_activity('portfolio', 30, ['apollo'], 'AI', 25e6, 'momentum')
    assert calls[0].pop('perspective') == 'company'
    assert calls[1].pop('perspective') == 'investor'
    assert calls[0] == calls[1] == dict(window_days=30, apollo_only=True, search='AI', min_size_usd=25e6, sort='momentum')


def test_tab_deep_link_and_lazy_news_reader():
    def find(component, wanted):
        if getattr(component, 'id', None) == wanted:
            return component
        children = getattr(component, 'children', None)
        for child in children if isinstance(children, list) else [children]:
            result = find(child, wanted) if child is not None else None
            if result is not None:
                return result
    assert find(vc_dashboard.create_vc_dashboard_layout('news')[1], 'vc-tabs').value == 'news'
    assert find(vc_dashboard.create_vc_dashboard_layout('invalid')[1], 'vc-tabs').value == 'funding'
    frame = find(vc_dashboard.create_vc_dashboard_layout('news')[1], 'vc-news-frame')
    assert getattr(frame, 'src', None) is None


def test_portfolio_membership_is_not_a_deal(monkeypatch):
    today = pd.Timestamp.now().normalize()
    frame = pd.DataFrame({'deal_id':['123-45T','inv-company-firm'], 'company':['A','B'],
        'company_pbid':['a','b'], 'investors':['','Firm'], 'source':['pitchbook_mcp','pitchbook_investor'],
        'deal_date':[today,today], 'deal_size_usd':[100,None], 'sector':['AI','AI']})
    monkeypatch.setattr(queries,'read_table',lambda *a,**kw:frame)
    assert [r['deal_id'] for r in queries.get_deals()] == ['123-45T']
    assert queries.get_summary()['deal_count'] == 1
    assert queries.get_portfolio()[0]['company'] == 'B'


def test_announced_values_do_not_enter_completed_value_chart(monkeypatch):
    today=pd.Timestamp.now().normalize()
    frame=pd.DataFrame({'deal_id':['done','announced'], 'deal_date':[today,today],
                        'deal_status':['Completed','Announced/In Progress'],
                        'deal_size_usd':[100,9999], 'sector':['AI','AI']})
    monkeypatch.setattr(queries,'_deals_df',lambda env:frame)
    result=queries.get_summary()
    assert result['deal_count']==2 and result['total_raised_usd']==100
    assert result['disclosed_count']==1 and result['source_stale']


def test_record_serialization_keeps_missing_values_null():
    assert queries._records(pd.DataFrame({'amount':[float('nan')], 'date':[pd.NaT]})) == [{'amount':None,'date':None}]


def test_numeric_deals_and_currency():
    rows = callbacks._deal_rows([{'deal_id': 'zero', 'deal_size_usd': 0},
                                {'deal_id': 'large', 'deal_size_usd': 1e9},
                                {'deal_id': 'missing', 'deal_size_usd': None}])
    assert [r['deal_size'] for r in rows] == [0, 1e9, None]
    assert formatting._usd(0) == '$0'
    assert formatting._usd(None) == '—'
    assert formatting._usd(1.5e9) == '$1.5B'
    assert formatting._usd(25000) == '$25K'
    assert formatting._usd(1.2e12) == '$1.2T'
    json.dumps(vc_dashboard.create_vc_dashboard_layout(), cls=PlotlyJSONEncoder)


def test_summary_compares_equal_windows_and_excludes_future(monkeypatch):
    today = pd.Timestamp.now().normalize()
    frame = pd.DataFrame({
        'deal_id': ['old', 'prior', 'first', 'today', 'future'],
        'deal_date': [today - pd.Timedelta(days=n) for n in [14, 7, 6, 0, -1]],
        'deal_size_usd': [900, 100, 0, 200, 1000],
        'sector': ['AI'] * 5,
    })
    monkeypatch.setattr(queries, '_deals_df', lambda env: frame)
    result = queries.get_summary(7)
    assert result['deal_count'] == 2
    assert result['previous_deal_count'] == 1
    assert result['total_raised_usd'] == 200
    assert result['previous_raised_usd'] == 100
    assert result['median_raised_usd'] == 100
    assert len(result['trend']) == 7
    assert sum(r['capital'] for r in result['trend']) == 200
    assert len(queries.get_deals(7)) == 2
    json.dumps(formatting.summary_charts(result), cls=PlotlyJSONEncoder)
    json.dumps(formatting.summary_cards(result), cls=PlotlyJSONEncoder)


def test_month_columns_are_chronological(monkeypatch):
    today = pd.Timestamp.now().normalize()
    frame = pd.DataFrame({'deal_id': ['1', '2', '3'], 'sector': ['AI'] * 3,
                          'deal_date': [today, today, today - pd.Timedelta(days=40)]})
    monkeypatch.setattr(queries, '_deals_df', lambda env: frame)
    result = queries.get_sector_heatmap(90, axis='time_bucket')
    assert result['buckets'] == sorted(result['buckets'])
    assert result['matrix'][0][-1] == 2


def test_empty_charts_and_scaled_heatmap():
    json.dumps(formatting.summary_charts({'deal_count': 0}), cls=PlotlyJSONEncoder)
    columns, rows, styles = formatting.heatmap_table({
        'sectors': ['AI'], 'buckets': ['Seed'], 'matrix': [[1.2e9]], 'metric': 'size'})
    assert rows[0]['Seed'] == 1.2
    assert columns[1]['format'].to_plotly_json()['locale']['symbol'] == ['$', 'B']


def test_cache_expires_and_overwrites_atomically(tmp_path, monkeypatch):
    from data_access.dbtables.vc import local_cache, tables
    from deltalake import DeltaTable
    monkeypatch.setenv('VC_CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(tables, 'read_table_databricks', lambda *a, **kw: pd.DataFrame({'deal_id': ['new']}))
    local_cache.build('dev', ['vc_deals'])
    path = local_cache._table_path('dev', 'vc_deals')
    local_cache.build('dev', ['vc_deals'])
    assert DeltaTable(path).version() == 1
    assert len(local_cache.read('vc_deals', 'dev')) == 1
    for commit in (tmp_path / 'vc_cache' / 'dev' / 'vc_deals' / '_delta_log').glob('*.json'):
        os.utime(commit, (time.time()-1000, time.time()-1000))
    assert local_cache.read('vc_deals', 'dev') is None


def test_size_bands_keep_every_bucket(monkeypatch):
    frame = pd.DataFrame({'deal_id': ['1', '2'], 'sector': ['AI', 'AI'],
                          'deal_date': [pd.Timestamp.now()] * 2,
                          'deal_size_band': ['$100-250M', '$250-500M']})
    monkeypatch.setattr(queries, '_deals_df', lambda env: frame)
    assert queries.get_sector_heatmap(90, axis='deal_size_band')['buckets'] == ['$100-250M', '$250-500M']


def test_news_queues_are_scoped_by_environment(tmp_path, monkeypatch):
    from data_importer.vc.news import wiki_env
    monkeypatch.setattr(wiki_env.os, 'environ', dict(os.environ))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setattr(wiki_env, 'resolve', lambda env: {'vault': str(tmp_path / env),
        'research': str(tmp_path), 'index': 'vc_news', 'es_url': 'http://localhost:9201'})
    monkeypatch.setattr(wiki_env, 'ensure_vault_config', lambda vault: None)
    wiki_env.apply('dev')
    dev = os.environ['ARGUS_WIKI_QUEUE_DIR']
    wiki_env.apply('prod')
    assert os.environ['ARGUS_RAW_ROOT'] == str(tmp_path)
    assert os.environ['ARGUS_WIKI_QUEUE_DIR'] != dev
    assert 'vc_news' in dev


def test_feed_fetch_uses_bounded_http(monkeypatch):
    import requests
    from data_importer.vc.news import rss_adapter
    class Response:
        content = b'<rss version="2.0"><channel><title>VC</title><item><title>Round</title><link>https://example.com/round</link></item></channel></rss>'
        def raise_for_status(self):
            pass
    seen = {}
    def get(url, **kwargs):
        seen.update(kwargs)
        return Response()
    monkeypatch.setattr(requests, 'get', get)
    articles = rss_adapter.fetch_source({'name': 'VC', 'type': 'media', 'url_or_handle': 'https://example.com/feed'})
    assert articles[0]['title'] == 'Round'
    assert seen['timeout'] == (5, 30)


def test_summary_tiles_are_value_and_count_only():
    cards = formatting.summary_cards({'deal_count': 3, 'total_raised_usd': 5e9})
    labels = [card.children[0].children for card in cards]
    assert labels == ['Total deal value', 'Number of deals']


def test_heatmap_gradient_uses_theme_colormix():
    _, _, styles = formatting.heatmap_table({
        'sectors': ['AI', 'Fintech'], 'buckets': ['Seed'], 'matrix': [[4], [1]], 'metric': 'count'})
    assert 'color-mix' in styles[0]['backgroundColor']
    assert 'var(--apollo-green)' in styles[0]['backgroundColor']
    assert styles[0]['color'] == '#ffffff'          # strongest cell reads on green
    assert styles[1]['color'] == 'var(--apollo-text)'


def test_parse_geography_variants():
    assert queries.parse_geography('San Francisco, CA') == ('United States', 'San Francisco, CA')
    assert queries.parse_geography('New York, NY, United States') == ('United States', 'New York, NY')
    assert queries.parse_geography('Tel Aviv, Israel') == ('Israel', 'Tel Aviv, Israel')
    assert queries.parse_geography('North America') == ('North America', 'North America')
    assert queries.parse_geography(None) == ('Unknown', 'Unknown')


def test_firm_deals_attribution(monkeypatch):
    today = pd.Timestamp.now().normalize()
    deals = pd.DataFrame({'deal_id': ['1-1T', '2-2T', '3-3T'], 'company': ['A', 'B', 'C'],
        'source': ['pitchbook_mcp'] * 3, 'deal_date': [today] * 3,
        'deal_size_usd': [100.0, 50.0, 25.0], 'sector': ['AI'] * 3,
        'investors': ['Sequoia Capital, Other Fund', 'Nobody Ventures', None]})
    firms = pd.DataFrame({'investor_name': ['Sequoia Capital', 'Accel']})
    monkeypatch.setattr(queries, '_deals_df', lambda env: deals)
    monkeypatch.setattr(queries, 'read_table', lambda table, **kw: firms)
    rows = queries.get_firm_deals(7)
    # Disclosed-but-unmatched participants drop; blank participants stay (watchlist-scoped pull).
    assert [r['deal_id'] for r in rows] == ['1-1T', '3-3T']
    assert rows[0]['followed_firms'] == 'Sequoia Capital'
    assert rows[1]['followed_firms'] == ''
    json.dumps([formatting.firm_deal_feed(rows)], cls=PlotlyJSONEncoder)
    json.dumps([formatting.firm_deal_feed([])], cls=PlotlyJSONEncoder)


def test_geo_breakdown_counts_and_completed_value(monkeypatch):
    today = pd.Timestamp.now().normalize()
    deals = pd.DataFrame({'deal_id': ['1', '2', '3'], 'deal_date': [today] * 3,
        'deal_status': ['Completed', 'Announced', 'Completed'],
        'deal_size_usd': [100.0, 900.0, 50.0], 'sector': ['AI'] * 3,
        'region': ['San Francisco, CA', 'Palo Alto, CA', 'Tel Aviv, Israel']})
    monkeypatch.setattr(queries, '_deals_df', lambda env: deals)
    geo = queries.get_geo_breakdown(7)
    us = next(r for r in geo['countries'] if r['country'] == 'United States')
    assert us['deals'] == 2 and us['value'] == 100.0   # announced value excluded
    assert len(geo['metros']) == 3
    json.dumps(formatting.geo_chart(geo['countries'], 'size'), cls=PlotlyJSONEncoder)
    json.dumps(formatting.geo_chart([], 'count'), cls=PlotlyJSONEncoder)


def test_investor_activity_aggregates_and_ranks(monkeypatch):
    today = pd.Timestamp.now().normalize()
    deals = pd.DataFrame({
        'deal_id': ['1-1T', '2-2T', '3-3T'],
        'company': ['A', 'B', 'C'],
        'source': ['pitchbook_mcp'] * 3,
        'deal_date': [today, today, today - pd.Timedelta(days=1)],
        'deal_status': ['Completed', 'Completed', 'Announced'],
        'deal_size_usd': [100.0, 900.0, 50.0],
        'sector': ['AI'] * 3,
        'investors': ['Sequoia Capital', 'Sequoia Capital, Accel', 'Accel'],
        'lead_investors': ['Sequoia Capital', '', ''],
    })
    def fake_read(table, **kw):
        if table == 'vc_investors':
            return pd.DataFrame({'investor_name': ['Sequoia Capital', 'Accel', 'GV']})
        return deals.assign(source='pitchbook_investor').head(0)
    monkeypatch.setattr(queries, '_deals_df', lambda env: deals)
    monkeypatch.setattr(queries, 'read_table', fake_read)
    firms = queries.get_investor_activity(30)
    # Tied on deal count -> completed value breaks the tie; inactive GV is dropped.
    assert [f['firm'] for f in firms] == ['Sequoia Capital', 'Accel']
    seq = firms[0]
    assert seq['deals'] == 2 and seq['led'] == 1 and seq['value_usd'] == 1000.0
    assert seq['top_deals'][0]['company'] == 'B'  # biggest sized deal first
    json.dumps([formatting.investor_activity(firms)], cls=PlotlyJSONEncoder)
    json.dumps([formatting.investor_activity([])], cls=PlotlyJSONEncoder)


def test_firm_drill_splits_syndicate_by_watchlist(monkeypatch):
    today = pd.Timestamp.now().normalize()
    deals = pd.DataFrame({
        'deal_id': ['1-1T', '2-2T'], 'company': ['A', 'B'],
        'source': ['pitchbook_mcp'] * 2, 'deal_date': [today] * 2,
        'deal_size_usd': [100.0, None], 'sector': ['AI'] * 2,
        'investors': ['Sequoia Capital, Accel, Outsider Fund',
                      'Sequoia Capital, Outsider Fund'],
    })
    monkeypatch.setattr(queries, '_deals_df', lambda env: deals)
    monkeypatch.setattr(queries, '_watchlist_names',
                        lambda env=None: ['Sequoia Capital', 'Accel'])
    drill = queries.get_firm_drill('Sequoia Capital', 30)
    co = {c['name']: c for c in drill['co_investors']}
    assert co['Outsider Fund']['n'] == 2 and not co['Outsider Fund']['followed']
    assert co['Accel']['n'] == 1 and co['Accel']['followed']
    assert 'Sequoia Capital' not in co
    json.dumps([formatting.firm_drill(drill)], cls=PlotlyJSONEncoder)

    candidates = queries.get_follow_candidates(30)
    assert [c['name'] for c in candidates] == ['Outsider Fund']  # >=2 shared deals
    json.dumps([formatting.follow_candidates(candidates)], cls=PlotlyJSONEncoder)


def test_hot_companies_step_up_and_ranking(monkeypatch):
    today = pd.Timestamp.now().normalize()
    deals = pd.DataFrame({
        'deal_id': ['1', '2', '3'], 'company': ['A', 'A', 'B'],
        'source': ['pitchbook_mcp'] * 3,
        'deal_date': [today - pd.Timedelta(days=400), today, today],
        'deal_status': ['Completed', 'Completed', 'Completed'],
        'deal_size_usd': [10.0, 100.0, 40.0],
        'post_valuation_usd': [1000.0, 3000.0, None],
        'round_type': ['Series A', 'Series B', 'Seed'], 'sector': ['AI'] * 3,
    })
    monkeypatch.setattr(queries, '_deals_df', lambda env: deals)
    rows = queries.get_hot_companies(30)
    assert [r['company'] for r in rows] == ['A', 'B']
    assert rows[0]['step_up'] == 3.0          # prior priced round outside the window counts
    assert rows[0]['raised_usd'] == 100.0     # window-scoped raise only
    json.dumps([formatting.hot_companies(rows)], cls=PlotlyJSONEncoder)
    json.dumps([formatting.hot_companies([])], cls=PlotlyJSONEncoder)


def test_sparkline_monthly_counts(monkeypatch):
    today = pd.Timestamp.now().normalize()
    trailing = pd.DataFrame({'deal_date': [today, today, today - pd.Timedelta(days=45)],
                             'investors': ['Accel'] * 3})
    counts = queries._monthly_counts(trailing, 'Accel')
    assert len(counts) == 12 and counts[-1] == 2 and sum(counts) == 3
    assert queries._monthly_counts(trailing.head(0), 'Accel') == [0] * 12
    json.dumps([formatting._sparkline(counts)], cls=PlotlyJSONEncoder)


def test_company_backers_size_filter_and_syndicate_flags(monkeypatch):
    today = pd.Timestamp.now().normalize()
    deals = pd.DataFrame({
        'deal_id': ['1-1T', '2-2T'], 'company': ['Big', 'Small'],
        'source': ['pitchbook_mcp'] * 2, 'deal_date': [today] * 2,
        'deal_size_usd': [200e6, 5e6], 'sector': ['AI'] * 2,
        'investors': ['Sequoia Capital, Outsider Fund', 'Accel'],
        'lead_investors': ['Sequoia Capital', ''],
    })
    monkeypatch.setattr(queries, '_deals_df', lambda env: deals)
    monkeypatch.setattr(queries, '_watchlist_names', lambda env=None: ['Sequoia Capital', 'Accel'])
    rows = queries.get_company_backers(30, min_size_usd=100e6)
    assert [r['company'] for r in rows] == ['Big']
    lead = rows[0]['backers'][0]
    assert lead == {'name': 'Sequoia Capital', 'lead': True, 'followed': True}
    assert rows[0]['backers'][1]['followed'] is False
    assert len(queries.get_company_backers(30)) == 2  # All sizes
    json.dumps([formatting.company_backers(rows)], cls=PlotlyJSONEncoder)
    json.dumps([formatting.company_backers([])], cls=PlotlyJSONEncoder)
