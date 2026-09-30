import logging
import os
from urllib import parse

import dash
import dash_cytoscape as cyto
from dash import Input, Output, callback, dash_table, dcc, html

from common.utils.envsetup import get_base_parser, init_app_using_parser
from dash_reports.data_catalog.pages import callbacks  # noqa: F401
from dash_reports.data_catalog.pages import catalog_page

ROOT_ID = 'catalog-root'


def _library_preload():
    return html.Div(
        [cyto.Cytoscape(id='_preload-cyto', elements=[]), dash_table.DataTable(id='_preload-table')],
        style={'display': 'none'},
    )


def _layout_for_search(search):
    query = dict(parse.parse_qsl(parse.urlsplit(search or '').query))
    values = lambda key: [value for value in query.get(key, '').split(',') if value] or None
    page = query.get('page', '1')
    return catalog_page.create_layout(
        initial_asset=query.get('asset'),
        initial_vendor=values('vendor'),
        initial_category=values('category'),
        initial_view=query.get('view', 'directory'),
        initial_catalog=values('catalog'),
        initial_schema=values('schema'),
        initial_source=values('source'),
        initial_asset_kind=values('kind'),
        initial_tags=query.get('tags', catalog_page.DEFAULT_TAG_FILTER),
        initial_governance=values('governance'),
        initial_query=query.get('q'),
        initial_page=int(page) if str(page).isdigit() else 1,
    )


@callback(Output(ROOT_ID, 'children'), Input('url', 'search'))
def _route(search):
    return _layout_for_search(search)


def _build_app():
    assets = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'assets'))
    app = dash.Dash(__name__, assets_folder=assets, title='Data Catalog (local)')
    app.config.suppress_callback_exceptions = True
    app.layout = html.Div([dcc.Location(id='url', refresh=False), _library_preload(), html.Div(id=ROOT_ID)])
    return app


def main():
    parser = get_base_parser('data catalog local', log_file='data_catalog_local')
    parser.add_argument('--port', default='8050')
    parser.add_argument('--host', default='127.0.0.1')
    args = init_app_using_parser(parser)
    app = _build_app()
    logging.info('Data Catalog local checkout: http://%s:%s/', args.host, args.port)
    app.run_server(host=args.host, port=int(args.port), debug=True, use_reloader=False)


if __name__ == '__main__':
    main()
