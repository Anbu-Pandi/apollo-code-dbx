from dash import Dash, html, dcc, Input, Output, State
from dash.exceptions import PreventUpdate

from dash_reports.report_common.elastic_search.api import ElasticSearchManager

es = ElasticSearchManager(None)
options = [
    {"label": "New York City", "value": "NYC"},
    {"label": "Montreal", "value": "MTL"},
    {"label": "San Francisco", "value": "SF"},
]

app = Dash(__name__,
           update_title='***Refreshing Data***',
           meta_tags=[{"name": "viewport", "content": "width=device-width"}, ])
app.title = 'Market Analytics Reports'
server = app.server
app.config.suppress_callback_exceptions = True

app.layout = html.Div([
    html.Div([
        "Single dynamic Dropdown",
        dcc.Dropdown(id="my-dynamic-dropdown")
    ]),
    html.Div([
        "Multi dynamic Dropdown",
        dcc.Dropdown(id="my-multi-dynamic-dropdown", multi=True),
    ]),
])


@app.callback(
    Output("my-dynamic-dropdown", "options"),
    Input("my-dynamic-dropdown", "search_value")
)
def update_options(search_value):
    if not search_value:
        raise PreventUpdate
    df = es.search_prefix(['companies'], ['companyname'], search_value)
    return [{'label': x['companyname'], 'value': int(x['id_bb_company'])} for _, x in df.iterrows()]


@app.callback(
    Output("my-multi-dynamic-dropdown", "options"),
    Input("my-multi-dynamic-dropdown", "search_value"),
    State("my-multi-dynamic-dropdown", "value")
)
def update_multi_options(search_value, value):
    if not search_value:
        raise PreventUpdate
    return [
        o for o in options if search_value in o["label"] or o["value"] in (value or [])
    ]


if __name__ == "__main__":
    from apo_config.config import config

    config.init('dev')

    from common.utils.logutil import setuplogger
    setuplogger()
    
    # df = sqlutil.readdfsql(
    #     'select cc.companyid, cc.companyname from ciqFinPeriod cfp join ciqCompany cc on cfp.companyid = cc.companyid  where fiscalYear > 2015',
    #     app='spglobal').drop_duplicates()
    #
    # es.add_df(df, 'companies')
    searched = es.search_prefix(['companies'], ['companyname'], 'phar')

    app.run_server(debug=False, host='0.0.0.0')
