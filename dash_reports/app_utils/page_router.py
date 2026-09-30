import logging
from importlib import import_module
from urllib import parse

from dash_reports.report_common.pages.link_and_headers import get_index_page_links
from dash_reports.report_common.data import ui_config
from dash_reports.report_common.utils import dash_authentication
from dash import html


class _LazyModule:
    def __init__(self, name):
        self.name = name
        self.module = None

    def __getattr__(self, name):
        if self.module is None:
            self.module = import_module(self.name)
        return getattr(self.module, name)


risk_report_page = _LazyModule('dash_reports.risk_report.pages.utils')
tracepage = _LazyModule('dash_reports.trace.pages.utils')
credit_movers = _LazyModule('dash_reports.issuer_debt_in_index_movers.pages.movers')
risk_data_center = _LazyModule('dash_reports.risk_data_center.pages.risk_data_center')
etf_vs_funds = _LazyModule('dash_reports.etf_apo_funds_report.pages.etf_fund_comparison')
risk_data_center_utils = _LazyModule('dash_reports.risk_data_center.pages.utils')
analyst_ques_utils = _LazyModule('dash_reports.analyst_questionnaire.pages.utils')
issuer_info_page = _LazyModule('dash_reports.issuers_info.pages.index')
debt_view_index = _LazyModule('dash_reports.issuer_debt_in_index.pages.utils')
pf_stats_page = _LazyModule('dash_reports.portfolio_stats.pages.portfolio_stats_page')
bm_perf_page = _LazyModule('dash_reports.benchmark_performance.pages.layout')
liquidity_report = _LazyModule('dash_reports.liquidity.pages.liquidity_report')
risk_query = _LazyModule('dash_reports.risk_query.pages.index')
go_private_form = _LazyModule('dash_reports.go_private_form.pages.go_private_form')
compliance = _LazyModule('dash_reports.compliance.pages.compliance')
macro_data = _LazyModule('dash_reports.macro_data.pages.macro_data')
trace_trd_summary = _LazyModule('dash_reports.trace_portfolio_analysis.pages.trace_trade_summary')
cdx_tranche_pricer_page = _LazyModule('dash_reports.cdx_tranche_pricer.pages.cdx_tranche_pricer')
dash_user_activity = _LazyModule('dash_reports.dash_user_activity.pages.summary')
genai_issues_page = _LazyModule('dash_reports.genai_issues.pages.summary')
genai_conversations_page = _LazyModule('dash_reports.genai_conversations.pages.explorer')
ai_chat_page = _LazyModule('dash_reports.ai_chat.pages.utils')
data_viewer_page = _LazyModule('dash_reports.data_viewer.pages.data_viewer_page')
data_catalog_page = _LazyModule('dash_reports.data_catalog.pages.catalog_page')
pred_tracker_page = _LazyModule('dash_reports.pred_tracker_viewer.pred_tracker_page')
cdx_options_page = _LazyModule('dash_reports.cdx_options.pages.cdx_options')
cdx_research_page = _LazyModule('dash_reports.cdx_research.pages')
rate_vol_page = _LazyModule('dash_reports.rate_vol_tracker.pages.rate_vol_page')
fx_vol_page = _LazyModule('dash_reports.fx_vol_tracker.pages.fx_vol_page')
cdx_vol_page = _LazyModule('dash_reports.cdx_vol_tracker.pages.cdx_vol_page')
vol_dashboard_page = _LazyModule('dash_reports.vol_market_dashboard.pages.dashboard_page')
industry_tracker_page = _LazyModule('dash_reports.industry_tracker.pages.industry_tracker_page')
cds_cash_basis_page = _LazyModule('dash_reports.cds_cash_basis.pages.cds_cash_basis_page')
vc_dashboard_page = _LazyModule('dash_reports.vc_dashboard.pages.vc_dashboard')
email_subscriptions_page = _LazyModule('dash_reports.subscriptions.page')


class PageRouter:
    def __init__(self, app):
        self.app = app
        self._header_routes = self._build_header_routes()
        self._content_routes = self._build_content_routes()
    
    def _build_header_routes(self):
        return {
            '/risk-report': lambda path, app: risk_report_page.get_risk_header(),
            '/trace': lambda path, app: tracepage.get_trace_header(),
            '/index': lambda path, app: [],
            '/data-center': lambda path, app: risk_data_center.get_data_center_headers(),
            '/etf-vs-apo-funds': lambda path, app: [],
            '/analyst-questionnaire': lambda path, app: analyst_ques_utils.get_questionnaire_headers(app),
            '/issuer-info': lambda path, app: issuer_info_page.get_issuer_info_header(path.split('issuer-info/')[-1]),
            '/issuer-debt-index': lambda path, app: debt_view_index.get_issuer_debt_index_header(),
            '/pf-stats': lambda path, app: pf_stats_page.get_pf_stats_header(),
            '/benchmark-perf': lambda path, app: bm_perf_page.get_bm_header(),
            '/liquidity-report': lambda path, app: liquidity_report.get_liquidity_headers(app),
            '/risk-query': lambda path, app: risk_query.get_headers(app),
            '/go-private-form': lambda path, app: go_private_form.get_headers(app),
            '/compliance': lambda path, app: compliance.get_headers(app),
            '/macro-data': lambda path, app: macro_data.get_macro_data_headers(app),
            '/dash-usage-report': lambda path, app: dash_user_activity.get_dash_usage_headers(app),
            '/genai-issues-report': lambda path, app: genai_issues_page.get_genai_issues_headers(app),
            '/genai-conversations': lambda path, app: genai_conversations_page.get_conversations_headers(app),
            '/trade-summary-trace': lambda path, app: trace_trd_summary.get_trade_summary_headers(app),
            '/cdx-tranche-pricer': lambda path, app: cdx_tranche_pricer_page.get_tranche_headers(app),
            '/ai-chat': lambda path, app: ai_chat_page.get_headers(app),
            '/data-viewer': lambda path, app: data_viewer_page.get_data_viewer_header(),
            '/data-catalog': lambda path, app: data_catalog_page.get_data_catalog_header(),
            '/pred-tracker': lambda path, app: [],  # No header for fullscreen iframe
            '/cdx-options': lambda path, app: cdx_options_page.get_cdx_options_headers(app),
            '/cdx-live-quotes': lambda path, app: cdx_research_page.header('quotes'),
            '/cdx-vol-model': lambda path, app: cdx_research_page.header('model'),
            '/cross-asset-correlation': lambda path, app: cdx_research_page.header('model'),
            '/rate-vol-tracker': lambda path, app: rate_vol_page.get_rate_vol_headers(app),
            '/fx-vol-tracker': lambda path, app: fx_vol_page.get_fx_vol_headers(app),
            '/cdx-vol-tracker': lambda path, app: cdx_vol_page.get_cdx_vol_headers(app),
            '/vol-dashboard': lambda path, app: vol_dashboard_page.get_dashboard_headers(app),
            '/industry-tracker': lambda path, app: industry_tracker_page.get_industry_tracker_headers(app),
            '/cds-cash-basis': lambda path, app: cds_cash_basis_page.get_cds_cash_basis_headers(app),
            '/vc-dashboard': lambda path, app: vc_dashboard_page.get_vc_dashboard_headers(app),
        }
    
    def _build_content_routes(self):
        return {
            '/risk-report': self._route_risk_report,
            '/trace': self._route_trace,
            '/index': self._route_index,
            '/data-center': self._route_data_center,
            '/etf-vs-apo-funds': self._route_etf_vs_funds,
            '/analyst-questionnaire': self._route_analyst_questionnaire,
            '/issuer-info': self._route_issuer_info,
            '/issuer-debt-index': self._route_issuer_debt_index,
            '/pf-stats': self._route_pf_stats,
            '/benchmark-perf': self._route_benchmark_perf,
            '/liquidity-report': self._route_liquidity_report,
            '/risk-query': self._route_risk_query,
            '/go-private-form': self._route_go_private_form,
            '/compliance': self._route_compliance,
            '/macro-data': self._route_macro_data,
            '/dash-usage-report': self._route_dash_usage,
            '/genai-issues-report': self._route_genai_issues,
            '/genai-conversations': self._route_genai_conversations,
            '/trade-summary-trace': self._route_trade_summary,
            '/cdx-tranche-pricer': self._route_cdx_tranche_pricer,
            '/ai-chat': self._route_ai_chat,
            '/data-viewer': self._route_data_viewer,
            '/data-catalog': self._route_data_catalog,
            '/pred-tracker': self._route_pred_tracker,
            '/cdx-options': self._route_cdx_options,
            '/cdx-live-quotes': lambda path, query, settings, groups: cdx_research_page.layout('quotes', query),
            '/cdx-vol-model': lambda path, query, settings, groups: cdx_research_page.layout('model', query),
            '/cross-asset-correlation': lambda path, query, settings, groups: cdx_research_page.layout('model', query),
            '/rate-vol-tracker': self._route_rate_vol_tracker,
            '/fx-vol-tracker': self._route_fx_vol_tracker,
            '/cdx-vol-tracker': self._route_cdx_vol_tracker,
            '/vol-dashboard': self._route_vol_dashboard,
            '/industry-tracker': self._route_industry_tracker,
            '/cds-cash-basis': self._route_cds_cash_basis,
            '/vc-dashboard': self._route_vc_dashboard,
            '/email-subscriptions': lambda path, query, settings, groups: ([], email_subscriptions_page.layout(query)),
        }
    
    def get_header_for_path(self, pathname):
        logging.info(f'on url request page: {pathname} changing header')
        
        for route_prefix, handler in self._header_routes.items():
            if pathname.startswith(route_prefix):
                return handler(pathname, self.app)
        
        return []
    
    def get_content_for_path(self, pathname, search, user, groups):
        logging.info(f'on url request user: {user} page: {pathname} params: {search} groups: {groups}')
        
        pathname = pathname or 'risk-report'
        query_params = dict(parse.parse_qsl(parse.urlsplit(search).query))
        user_settings = ui_config.get_page_config_by_user(user, pathname)
        
        page_content_static_html = []
        page_content_dynamic_html = []
        
        if not dash_authentication.dash_access_check(groups, pathname, access_level='read'):
            page_content_static_html = [html.Br()]
            page_content_dynamic_html = [html.H2("This page is restricted. "
                                                 "Please contact Henry Kim to request access.",
                                                 style={'color': 'red'})]
            return page_content_static_html, page_content_dynamic_html
        
        for route_prefix, handler in self._content_routes.items():
            if pathname.startswith(route_prefix):
                return handler(pathname, query_params, user_settings, groups)
        
        return get_index_page_links(), []
    
    def _route_risk_report(self, pathname, query_params, user_settings, groups):
        dynamic = risk_report_page.create_risk_specific_page_layout(
            pathname.split('risk-report/')[-1],
            query_params.get('fund', None))
        return [], dynamic
    
    def _route_trace(self, pathname, query_params, user_settings, groups):
        dynamic = tracepage.create_traceflow_layout(self.app, pathname.split('trace/')[-1])
        return [], dynamic
    
    def _route_index(self, pathname, query_params, user_settings, groups):
        dynamic = credit_movers.create_layout(self.app, user_settings=user_settings)
        return [], dynamic
    
    def _route_data_center(self, pathname, query_params, user_settings, groups):
        dynamic = risk_data_center_utils.create_data_center_layout(
            self.app, pathname.split('data-center/')[-1])
        return [], dynamic
    
    def _route_etf_vs_funds(self, pathname, query_params, user_settings, groups):
        dynamic = etf_vs_funds.create_layout()
        return [], dynamic
    
    def _route_analyst_questionnaire(self, pathname, query_params, user_settings, groups):
        static, dynamic = analyst_ques_utils.get_ques_specific_layout(
            pathname.split('analyst-questionnaire/')[-1],
            query_params.get('id_bb_company', -1))
        return static, dynamic
    
    def _route_issuer_info(self, pathname, query_params, user_settings, groups):
        static, dynamic = issuer_info_page.create_layout(
            pathname.split('issuer-info/')[-1],
            query_params.get('id_bb_company', -1), 
            user_settings=user_settings)
        return static, dynamic
    
    def _route_issuer_debt_index(self, pathname, query_params, user_settings, groups):
        static, dynamic = debt_view_index.create_layout(
            pathname.split('issuer-debt-index/')[-1],
            query_params.get('bonds', None),
            user_settings=user_settings)
        return static, dynamic
    
    def _route_pf_stats(self, pathname, query_params, user_settings, groups):
        dynamic = pf_stats_page.create_layout()
        return [], dynamic
    
    def _route_benchmark_perf(self, pathname, query_params, user_settings, groups):
        static, dynamic = bm_perf_page.create_layout()
        return static, dynamic
    
    def _route_liquidity_report(self, pathname, query_params, user_settings, groups):
        dynamic = liquidity_report.get_section_specific_layout(
            pathname.split('liquidity-report/')[-1])
        return [], dynamic
    
    def _route_risk_query(self, pathname, query_params, user_settings, groups):
        static, dynamic = risk_query.create_risk_query_layout(
            self.app, 
            pathname.split('risk-query/')[-1], 
            user_settings=user_settings,
            groups=groups)
        return static, dynamic
    
    def _route_go_private_form(self, pathname, query_params, user_settings, groups):
        static, dynamic = go_private_form.create_go_private_form_layout(
            self.app, pathname.split('go-private-form/')[-1])
        return static, dynamic
    
    def _route_compliance(self, pathname, query_params, user_settings, groups):
        static, dynamic = compliance.create_compliance_layout(
            pathname.split('compliance/')[-1])
        return static, dynamic
    
    def _route_macro_data(self, pathname, query_params, user_settings, groups):
        static, dynamic = macro_data.create_macro_data_layout()
        return static, dynamic
    
    def _route_dash_usage(self, pathname, query_params, user_settings, groups):
        static = dash_user_activity.create_dash_usage_layout()
        return static, []

    def _route_genai_issues(self, pathname, query_params, user_settings, groups):
        static = genai_issues_page.create_genai_issues_layout()
        return static, []

    def _route_genai_conversations(self, pathname, query_params, user_settings, groups):
        # ?id=<session/run/conversation> deep-links straight to that replay
        initial_id = query_params.get('id') if query_params else None
        dynamic = genai_conversations_page.create_conversations_layout(initial_id=initial_id)
        return [], dynamic
    
    def _route_trade_summary(self, pathname, query_params, user_settings, groups):
        static, dynamic = trace_trd_summary.create_trade_summary_layout()
        return static, dynamic
    
    def _route_cdx_tranche_pricer(self, pathname, query_params, user_settings, groups):
        static, dynamic = cdx_tranche_pricer_page.create_tranche_pricer_layout()
        return static, dynamic
    
    def _route_ai_chat(self, pathname, query_params, user_settings, groups):
        dynamic = ai_chat_page.get_layout(pathname.split('ai-chat/')[-1])
        return [], dynamic
    
    def _route_data_viewer(self, pathname, query_params, user_settings, groups):
        dynamic = data_viewer_page.create_layout()
        return [], dynamic

    def _route_data_catalog(self, pathname, query_params, user_settings, groups):
        values = lambda key: [value for value in query_params.get(key, '').split(',') if value] or None
        page = query_params.get('page', '1')
        dynamic = data_catalog_page.create_layout(
            initial_asset=query_params.get('asset'),
            initial_vendor=values('vendor'),
            initial_category=values('category'),
            initial_view=query_params.get('view', 'directory'),
            initial_catalog=values('catalog'),
            initial_schema=values('schema'),
            initial_source=values('source'),
            initial_asset_kind=values('kind'),
            initial_tags=query_params.get('tags', data_catalog_page.DEFAULT_TAG_FILTER),
            initial_governance=values('governance'),
            initial_query=query_params.get('q'),
            initial_page=int(page) if str(page).isdigit() else 1,
        )
        return [], dynamic
    
    def _route_pred_tracker(self, pathname, query_params, user_settings, groups):
        layout = pred_tracker_page.layout
        return [], [layout]
    
    def _route_cdx_options(self, pathname, query_params, user_settings, groups):
        static, dynamic = cdx_options_page.create_cdx_options_layout()
        return static, dynamic
    
    def _route_rate_vol_tracker(self, pathname, query_params, user_settings, groups):
        static, dynamic = rate_vol_page.create_rate_vol_layout()
        return static, dynamic

    def _route_fx_vol_tracker(self, pathname, query_params, user_settings, groups):
        static, dynamic = fx_vol_page.create_fx_vol_layout()
        return static, dynamic

    def _route_cdx_vol_tracker(self, pathname, query_params, user_settings, groups):
        static, dynamic = cdx_vol_page.create_cdx_vol_layout()
        return static, dynamic

    def _route_vol_dashboard(self, pathname, query_params, user_settings, groups):
        static, dynamic = vol_dashboard_page.create_dashboard_layout()
        return static, dynamic

    def _route_industry_tracker(self, pathname, query_params, user_settings, groups):
        initial = query_params.get('industry')
        if not initial and '/industry-tracker/' in pathname:
            initial = pathname.split('industry-tracker/')[-1]
        dynamic = industry_tracker_page.create_industry_tracker_layout(initial_industry=initial or None)
        return [], dynamic

    def _route_cds_cash_basis(self, pathname, query_params, user_settings, groups):
        static, dynamic = cds_cash_basis_page.create_cds_cash_basis_layout()
        return static, dynamic

    def _route_vc_dashboard(self, pathname, query_params, user_settings, groups):
        static, dynamic = vc_dashboard_page.create_vc_dashboard_layout(
            query_params.get('tab', 'funding'), query_params.get('entity'), query_params.get('name'), query_params)
        return static, dynamic
