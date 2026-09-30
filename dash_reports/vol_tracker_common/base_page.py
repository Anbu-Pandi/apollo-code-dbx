from abc import ABC, abstractmethod
from typing import Optional, List, Tuple

import dash_core_components as dcc
import dash_html_components as html
import pandas as pd
import plotly.graph_objects as go

from common.utils.dtutil import today, dateadd
from dash_reports.report_common.pages.link_and_headers import get_page_title
from dash_reports.report_common.utils.components import get_datepicker_control, make_loading
from dash_reports.vol_tracker_common.base_config import DomainConfig, RVOL_WINDOWS, LOOKBACK_OPTIONS
from dash_reports.vol_tracker_common.styles import (
    ACCENT_COLOR, APOLLO_COLORS, CHART_LAYOUT, STY,
    TAB_BORDER, TAB_BORDER_THICK, SPACING_MD, SECTION_DIVIDER,
    MATRIX_TAB_STYLE, MATRIX_TAB_SELECTED,
    ENTITY_TAB_STYLE, ENTITY_TAB_SELECTED,
    PRODUCT_TAB_STYLE, PRODUCT_TAB_SELECTED,
    make_filter, arrow, delta_color,
    make_ts_chart, make_recent_table, build_summary_box,
    build_focus_chart_wrapper, build_grid_style, build_signal_matrix_html,
    build_xccy_row, build_product_tab_content,
    build_static_layout,
)
from dash_reports.vol_tracker_common.signal_matrix import conviction_opacity


class VolTrackerPage(ABC):
    @property
    @abstractmethod
    def config(self):
        pass

    @property
    def prefix(self) -> str:
        return self.config.prefix

    @property
    def page_title(self) -> str:
        return self.config.page_title

    @property
    def entity_label(self) -> str:
        return self.config.entity_label

    @property
    def output_div(self) -> str:
        return f'{self.prefix}_output'

    @property
    def submit_id(self) -> str:
        return f'{self.prefix}_submit'

    @property
    def date_id(self) -> str:
        return f'{self.prefix}_asof_date'

    @property
    def lookback_id(self) -> str:
        return f'{self.prefix}_lookback'

    @property
    def entity_id(self) -> str:
        return f'{self.prefix}_entity'

    @property
    def rvol_window_id(self) -> str:
        return f'{self.prefix}_rvol_window'

    @property
    def vol_unit_id(self) -> str:
        return f'{self.prefix}_vol_unit'

    @property
    def entity_tabs_id(self) -> str:
        return f'{self.prefix}_entity_tabs'

    @property
    def product_tabs_type(self) -> str:
        return f'{self.prefix}_product_tabs'

    @property
    def focus_chart_id(self) -> str:
        return f'{self.prefix}_focus_chart'

    @property
    def matrix_tabs_id(self) -> str:
        return f'{self.prefix}_matrix_tabs'

    @property
    def signal_cell_type(self) -> str:
        return f'{self.prefix}-signal-cell'

    @abstractmethod
    def get_entity_options(self) -> List[dict]:
        pass

    @abstractmethod
    def get_default_entities(self) -> list:
        pass

    @abstractmethod
    def load_entity_data(self, entity: str, asof_date, lookback: str) -> dict:
        pass

    @abstractmethod
    def build_signal_matrix_data(self, all_entity_data: dict, domain: DomainConfig) -> dict:
        pass

    def build_entity_selector(self, options: list, default: list) -> html.Div:
        return dcc.Dropdown(
            id=self.entity_id,
            options=options,
            value=default,
            multi=True,
            style={'width': '480px'},
            clearable=False,
        )

    def build_extra_primary_filters(self) -> Optional[List[html.Div]]:
        return None

    def build_advanced_filters(self) -> Optional[html.Div]:
        return html.Div([
            make_filter('Realized Vol Window', dcc.Dropdown(
                id=self.rvol_window_id,
                options=[{'label': k, 'value': v} for k, v in RVOL_WINDOWS.items()],
                value=21, style={'width': '80px'}, clearable=False,
            )),
            make_filter('Vol Display', dcc.Dropdown(
                id=self.vol_unit_id,
                options=self.config.vol_unit_options,
                value=self.config.vol_unit_options[0]['value'],
                style={'width': '140px'}, clearable=False,
            )),
        ], style={**STY['filter_row'], 'marginTop': '8px'})

    def get_headers(self, app=None) -> html.Div:
        if app is not None:
            app.title = self.page_title
        return html.Div([get_page_title(self.page_title), html.Br()])

    def create_layout(self) -> Tuple[html.Div, html.Div]:
        dt = dateadd(today(), '-1b')
        options = self.get_entity_options()
        default = self.get_default_entities()

        primary_filters = [
            make_filter('As Of', get_datepicker_control(
                dt=dt, id=self.date_id, disabled=False, style={'marginRight': '4px'})),
            make_filter('Lookback', dcc.Dropdown(
                id=self.lookback_id,
                options=[{'label': lb, 'value': lb} for lb in LOOKBACK_OPTIONS],
                value='1y', style={'width': '80px'}, clearable=False,
            )),
            make_filter(self.entity_label, self.build_entity_selector(options, default)),
        ]

        extra = self.build_extra_primary_filters()
        if extra:
            primary_filters.extend(extra)

        primary_row = html.Div(primary_filters, style=STY['filter_row'])
        advanced_row = self.build_advanced_filters()

        static_layout = build_static_layout(primary_row, advanced_row, self.submit_id)
        dynamic_layout = make_loading(html.Div(children=[], className='page', id=self.output_div))
        return static_layout, dynamic_layout

    def get_vol_params(self, vol_unit: str) -> Tuple[float, str]:
        vol_mult = self.config.vol_unit_scales.get(vol_unit, 1.0)
        opts = {o['value']: o['label'] for o in self.config.vol_unit_options}
        vol_label = opts.get(vol_unit, vol_unit)
        if '(' in vol_label:
            vol_label = vol_label.split('(')[0].strip()
        return vol_mult, vol_label

    def scale_df(self, df: pd.DataFrame, key: str, vol_mult: float = 1.0) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        scale = self.config.product_scale.get(key, 1)
        if key in self.config.vol_product_keys:
            scale *= vol_mult
        return df * scale if scale != 1 else df

    def vol_yaxis(self, key: str, vol_label: str) -> str:
        base = self.config.product_yaxis_base.get(key, '')
        if key in self.config.vol_product_keys:
            return vol_label
        return base

    def domain_for_inst(self, inst: str) -> DomainConfig:
        data_key = self.config.all_instruments[inst][0]
        return self.config.domain_by_data_key.get(data_key, self.config.domains[0])

    def build_focus_chart(self, entity: str, inst: str, ts, vol_mult: float = 1.0,
                          vol_label: str = 'bpd', roll_dates: list = None) -> html.Div:
        if ts is None or ts.dropna().empty:
            return html.Div()

        ts = ts.dropna()
        domain = self.domain_for_inst(inst)
        _, _, inst_type = self.config.all_instruments[inst]
        product_label = dict(self.config.product_tabs).get(
            self.config.inst_to_product.get(inst, ''), '')

        scale = domain.scale_for(inst_type)
        y_unit = domain.unit_for(inst_type)
        if domain.name == 'vols':
            scale *= vol_mult
            y_unit = vol_label
        y_vals = ts.values * scale

        y_min, y_max = float(y_vals.min()), float(y_vals.max())
        pad = max((y_max - y_min) * 0.08, 0.01)

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=ts.index, y=y_vals, mode='lines', name=f'{entity} {inst}',
            line=dict(width=2.5, color=APOLLO_COLORS[0]),
        ))

        if roll_dates:
            for rd in roll_dates:
                rd_ms = int(pd.Timestamp(rd).timestamp() * 1000)
                fig.add_shape(type='line', x0=rd_ms, x1=rd_ms, y0=0, y1=1,
                              xref='x', yref='paper',
                              line=dict(dash='dot', color='#aaa', width=1))
                fig.add_annotation(x=rd_ms, y=1, xref='x', yref='paper',
                                   text='roll', showarrow=False,
                                   font=dict(size=8, color='#999'), yshift=8)

        layout = {**CHART_LAYOUT, 'height': 340,
                  'title': dict(text=f'{entity} — {inst} ({product_label})',
                                font=dict(size=14, color='#2c3e50')),
                  'yaxis': {**CHART_LAYOUT.get('yaxis', {}),
                            'title': y_unit, 'range': [y_min - pad, y_max + pad]}}
        fig.update_layout(**layout)

        return build_focus_chart_wrapper(entity, inst, product_label, fig)

    def build_signal_cell_div(self, cell: dict, entity: str, inst: str,
                              is_most_confident: bool = False,
                              vol_mult: float = 1.0, vol_label: str = 'bpd') -> html.Div:
        cell_id = {'type': self.signal_cell_type, 'index': f'{entity}|{inst}'}

        if cell is None:
            return html.Div('\u2014', id=cell_id, n_clicks=0,
                            style={**STY['matrix_cell'], 'color': '#ccc', 'textAlign': 'center',
                                   'border': '1px solid #e2e6ea'})

        opacity = conviction_opacity(cell['conviction'])
        bg = cell['regime_color']
        r, g, b = int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16)

        style = {
            **STY['matrix_cell'],
            'backgroundColor': f'rgba({r},{g},{b},{opacity})',
            'border': '2px solid #d4a017' if is_most_confident else '1px solid #e2e6ea',
            'boxShadow': '0 0 8px rgba(212,160,23,0.4)' if is_most_confident else 'none',
        }

        is_vol = cell['level_unit'] in ('bpd', '%')
        mult = vol_mult if is_vol else 1.0
        use_pct = cell.get('use_pct', False)
        lv = cell['level_display'] * mult

        if use_pct:
            level_str = f'{lv:.4f}' if lv < 10 else f'{lv:.2f}'
            dm = cell['delta_1m'] * 100
            dw = cell['delta_1w'] * 100
            dm_str = f'1m: {arrow(dm)} {dm:+.2f}%'
            dw_str = f'1w: {arrow(dw)} {dw:+.2f}%'
        elif is_vol:
            dm = cell['delta_1m_bp'] * mult
            dw = cell['delta_1w_bp'] * mult
            level_str = f'{lv:.2f}{vol_label}' if '%' in vol_label else f'{lv:.1f} {vol_label}'
            dm_str = f'1m: {arrow(dm)} {dm:+.1f} {vol_label}'
            dw_str = f'1w: {arrow(dw)} {dw:+.1f} {vol_label}'
        else:
            dm = cell['delta_1m_bp']
            dw = cell['delta_1w_bp']
            unit = cell['level_unit'] or 'bp'
            level_str = f'{lv:.1f} {unit}'
            dm_str = f'1m: {arrow(dm)} {dm:+.1f} {unit}'
            dw_str = f'1w: {arrow(dw)} {dw:+.1f} {unit}'

        return html.Div([
            html.Div(level_str, style={'fontWeight': '700', 'fontSize': '13px', 'color': '#1a1a2e'}),
            html.Div(dm_str, style={'color': delta_color(dm), 'fontWeight': '600', 'marginTop': '2px'}),
            html.Div(dw_str, style={'color': delta_color(dw), 'marginTop': '1px'}),
            html.Div(cell['regime'], style={
                **STY['regime_tag'], 'backgroundColor': cell['regime_color'], 'color': 'white'}),
            html.Div(cell['watch'], style=STY['watch']),
        ], id=cell_id, n_clicks=0, style=style)

    def build_matrix_grid(self, matrix_data: dict, vol_mult: float = 1.0,
                          vol_label: str = 'bpd', entity_col_width: str = '80px') -> html.Div:
        cells = matrix_data['cells']
        most_confident = matrix_data['most_confident']
        instruments = matrix_data['instruments']
        entities = list(cells.keys())

        grid_children = [html.Div('')]
        grid_children += [html.Div(inst, style=STY['matrix_header']) for inst in instruments]

        for entity in entities:
            grid_children.append(html.Div(entity, style={**STY['entity_label'], 'fontSize': '11px'}))
            for inst in instruments:
                cell = cells[entity].get(inst)
                is_best = most_confident.get(inst) == entity
                grid_children.append(self.build_signal_cell_div(
                    cell, entity, inst, is_most_confident=is_best,
                    vol_mult=vol_mult, vol_label=vol_label))

        grid = html.Div(grid_children, style=build_grid_style(len(instruments), entity_col_width))
        xccy_items = self.build_xccy_items(matrix_data, vol_mult, vol_label)
        xccy = build_xccy_row(xccy_items, entity_col_width) if xccy_items else html.Div()
        return build_signal_matrix_html(grid, xccy)

    def build_xccy_items(self, matrix_data: dict, vol_mult: float, vol_label: str) -> List[Tuple[str, str]]:
        return []

    def build_matrix_tab(self, domain: DomainConfig, all_entity_data: dict,
                         vol_mult: float = 1.0, vol_label: str = 'bpd',
                         entity_col_width: str = '80px') -> dcc.Tab:
        matrix_data = self.build_signal_matrix_data(all_entity_data, domain)
        vm = vol_mult if domain.name == 'vols' else 1.0
        vl = vol_label if domain.name == 'vols' else 'bp'
        return dcc.Tab(
            label=domain.tab_label, value=domain.name,
            style=MATRIX_TAB_STYLE, selected_style=MATRIX_TAB_SELECTED,
            children=[
                build_summary_box(matrix_data),
                self.build_matrix_grid(matrix_data, vm, vl, entity_col_width),
            ],
        )

    @abstractmethod
    def get_display_df(self, data: dict, key: str, filters: dict, vol_mult: float) -> pd.DataFrame:
        pass

    def generate_result_tabs(self, asof_date, lookback: str, entities: list, filters: dict) -> html.Div:
        all_entity_data = {e: self.load_entity_data(e, asof_date, lookback) for e in entities}

        vol_unit = filters.get('vol_unit', self.config.vol_unit_options[0]['value'])
        vol_mult, vol_label = self.get_vol_params(vol_unit)

        matrix_tabs = dcc.Tabs(
            [self.build_matrix_tab(d, all_entity_data, vol_mult, vol_label,
                                   self.get_entity_col_width())
             for d in self.config.domains],
            id=self.matrix_tabs_id, value=self.config.domains[0].name,
            style={'borderBottom': TAB_BORDER, 'marginBottom': '8px'},
        )

        entity_tabs = []
        for entity in entities:
            data = all_entity_data[entity]
            roll_dates = self.get_roll_dates(data)

            product_tabs = []
            for key, label in self.config.product_tabs:
                display_df = self.get_display_df(data, key, filters, vol_mult)
                product_tabs.append(
                    dcc.Tab(
                        label=label, value=key,
                        style=PRODUCT_TAB_STYLE, selected_style=PRODUCT_TAB_SELECTED,
                        children=[build_product_tab_content(
                            make_ts_chart(display_df, f'{entity} - {label}',
                                          yaxis_title=self.vol_yaxis(key, vol_label),
                                          roll_dates=roll_dates),
                            make_recent_table(display_df),
                        )],
                    )
                )

            entity_tabs.append(dcc.Tab(
                label=entity, value=entity,
                style=ENTITY_TAB_STYLE, selected_style=ENTITY_TAB_SELECTED,
                children=[
                    dcc.Tabs(product_tabs, id={'type': self.product_tabs_type, 'ccy': entity},
                             value=self.config.default_product, style={'borderBottom': TAB_BORDER}),
                ]))

        return html.Div([
            matrix_tabs,
            html.Div(id=self.focus_chart_id),
            html.Div(style=SPACING_MD),
            html.Div(style=SECTION_DIVIDER),
            dcc.Tabs(entity_tabs, id=self.entity_tabs_id,
                     value=entities[0] if entities else None,
                     style={'borderBottom': TAB_BORDER_THICK}),
        ])

    def get_entity_col_width(self) -> str:
        return '80px'

    def get_roll_dates(self, data: dict) -> list:
        return None
