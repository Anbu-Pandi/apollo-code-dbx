import logging
from html import escape
from flask import Response, abort, jsonify

from dash_reports.industry_tracker.constants import FLASK_HTML_PATH


def setup_industry_tracker_proxy(server):
    @server.route(f'{FLASK_HTML_PATH}/<industry>', methods=['GET'])
    def industry_tracker_html(industry: str):
        from dash_reports.industry_tracker.data.universe import get_industry
        from dash_reports.sub_servers.industry_tracker_server.client import get_client
        if get_industry(industry) is None:
            return Response(_unknown_html(industry), status=404, content_type='text/html')
        try:
            data = get_client().get_html(industry)
        except Exception as e:
            logging.exception('Failed to fetch HTML for industry %s', industry)
            return Response(_error_html(str(e)), status=503, content_type='text/html')

        html_str = (data or {}).get('html') or ''
        if not html_str:
            return Response(_empty_html(industry), status=200, content_type='text/html')
        return Response(html_str, status=200, content_type='text/html; charset=utf-8')

    @server.route(f'{FLASK_HTML_PATH}/<industry>/asof', methods=['GET'])
    def industry_tracker_asof(industry: str):
        from dash_reports.industry_tracker.data.universe import get_industry
        from dash_reports.sub_servers.industry_tracker_server.client import get_client
        if get_industry(industry) is None:
            return jsonify({'error': f'Unknown industry: {industry}'}), 404
        try:
            data = get_client().get_html(industry)
            return jsonify({'industry': industry, 'asof': (data or {}).get('asof')})
        except Exception as e:
            logging.exception('Failed to fetch asof for %s', industry)
            return jsonify({'error': str(e)}), 503

    @server.route(f'{FLASK_HTML_PATH}/refresh/<industry>', methods=['POST'])
    def industry_tracker_refresh(industry: str):
        from dash_reports.industry_tracker.data.universe import get_industry
        from dash_reports.sub_servers.industry_tracker_server.client import get_client
        if get_industry(industry) is None:
            return jsonify({'error': f'Unknown industry: {industry}'}), 404
        try:
            return jsonify(get_client().refresh(industries=[industry]) or {})
        except Exception as e:
            logging.exception('Refresh failed for %s', industry)
            return jsonify({'error': str(e)}), 503

    @server.route(f'{FLASK_HTML_PATH}/status', methods=['GET'])
    def industry_tracker_status():
        from dash_reports.sub_servers.industry_tracker_server.client import get_client
        try:
            return jsonify(get_client().health_check() or {})
        except Exception as e:
            logging.exception('Failed to fetch industry tracker status')
            return jsonify({'error': str(e)}), 503

    logging.info('Industry Tracker proxy registered: %s/<industry>', FLASK_HTML_PATH)


def _error_html(msg: str) -> str:
    return f"""<!DOCTYPE html><html><body style="font-family:Calibri,sans-serif;padding:40px;
text-align:center;color:#55585A">
<h2 style="color:#007B63;margin-bottom:6px">Industry Tracker Server Unavailable</h2>
<p>The industry-tracker sub-server (default port 8030) is not reachable.</p>
<p style="font-size:11px;font-style:italic;color:#999">Error: {msg}</p>
<p style="font-size:11px;margin-top:14px">Start it with:
<code>python -m dash_reports.sub_servers.industry_tracker_server</code></p>
</body></html>"""


def _empty_html(industry: str) -> str:
    return f"""<!DOCTYPE html><html><body style="font-family:Calibri,sans-serif;padding:40px;
text-align:center;color:#55585A">
<h2 style="color:#007B63;margin-bottom:6px">Building {industry.title()} Tracker...</h2>
<p>Initial HTML cache is being constructed. This page will reload automatically.</p>
<script>setTimeout(function(){{window.location.reload();}}, 4000);</script>
</body></html>"""


def _unknown_html(industry: str) -> str:
    industry = escape(industry or '')
    return f"""<!DOCTYPE html><html><body style="font-family:Calibri,sans-serif;padding:40px;
text-align:center;color:#55585A">
<h2 style="color:#C85250;margin-bottom:6px">Unknown Industry Tracker</h2>
<p>No tracker is configured for <code>{industry}</code>.</p>
<p style="font-size:11px;margin-top:14px">Use the Industry dropdown or check
<code>data/universes/*.json</code> for configured tracker keys.</p>
</body></html>"""
