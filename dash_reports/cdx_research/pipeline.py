"""Render each result immediately; notebook execution is never needed at runtime."""
import json

import pandas as pd
import plotly.graph_objects as go

from dash_reports.report_common.pages import plotly_theme  # noqa: F401


def table_panel(title, frame, note=''):
    frame = frame.copy()
    frame.columns = [str(c) for c in frame.columns]
    for column in frame:
        if pd.api.types.is_datetime64_any_dtype(frame[column]):
            frame[column] = frame[column].map(lambda value: value.isoformat() if pd.notna(value) else None)
    return {'title': title, 'note': note, 'table': json.loads(frame.to_json(orient='records', date_format='iso')),
            'columns': list(frame.columns)}


def figure_panel(title, figure, note=''):
    margin = dict(l=55, r=40, t=55, b=65) | figure.layout.margin.to_plotly_json()
    figure.update_layout(template='plotly_white+apollo', autosize=True, width=None,
                         uirevision=title, margin=margin)
    return {'title': title, 'note': note, 'figure': json.loads(figure.to_json())}


def lines(title, frame, note='', selected=None):
    fig = go.Figure()
    for col in frame:
        fig.add_scatter(x=frame.index, y=frame[col], name=str(col), mode='lines')
    fig.update_layout(title=title, height=440, hovermode='x unified')
    result = figure_panel(title, fig, note)
    data = frame.copy()
    data.columns = [str(c) for c in data.columns]
    data.index.name = data.index.name or 'date'
    result.update({k: v for k, v in table_panel(title, data.reset_index()).items() if k in ('table', 'columns')})
    result['selected'] = selected if selected is not None else list(data.columns)
    return result


def run(kind, params, market=None):
    if kind == 'quotes':
        from dash_reports.cdx_research.quotes import run_quotes
        yield from run_quotes(params, market)
    else:
        from dash_reports.cdx_research.vol_model import run_model
        yield from run_model(params)
