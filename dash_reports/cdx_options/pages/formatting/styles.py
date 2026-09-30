STATUS_BAR_STYLE = {
    'position': 'fixed', 'bottom': '0', 'left': '0', 'right': '0',
    'backgroundColor': '#0d1117', 'color': '#c9d1d9',
    'fontFamily': 'Consolas, "Courier New", monospace', 'fontSize': '11px',
    'padding': '0',
    'zIndex': '9999', 'borderTop': '2px solid #006D77',
}

STATUS_BAR_HEADER_STYLE = {
    'color': '#58a6ff', 'fontWeight': 'bold', 'fontSize': '10px',
    'padding': '4px 12px 2px 12px', 'letterSpacing': '1px',
    'borderBottom': '1px solid #21262d',
    'cursor': 'pointer', 'userSelect': 'none',
    'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center',
}

STATUS_BAR_BODY_STYLE = {
    'overflowY': 'auto', 'padding': '2px 12px 4px 12px', 'maxHeight': '80px',
}

STATUS_CATEGORY_COLORS = {
    'start': '#58a6ff',
    'done': '#3fb950',
    'query': '#c9d1d9',
    'compute': '#c9d1d9',
    'data': '#3fb950',
    'light': '#d29922',
    'error': '#f85149',
    'render': '#8b949e',
    'info': '#8b949e',
}

THEME = {
    'page': '#f4f7fb',
    'surface': '#ffffff',
    'surface_soft': '#f8fafd',
    'border': '#d9e2ec',
    'border_strong': '#b8c7d9',
    'ink': '#172033',
    'muted': '#65758b',
    'faint': '#8fa0b5',
    'accent': '#006D77',
    'accent_soft': '#e6f4f5',
    'blue': '#0b57a4',
    'blue_soft': '#edf5ff',
    'green': '#1b7a3d',
    'green_soft': '#edf8f0',
    'red': '#c0392b',
    'red_soft': '#fdeeee',
    'amber': '#8a6d1d',
    'amber_soft': '#fff8db',
    'shadow': '0 10px 28px rgba(15, 35, 55, 0.08)',
    'shadow_soft': '0 2px 8px rgba(15, 35, 55, 0.06)',
}

PAGE_SHELL_STYLE = {
    'backgroundColor': THEME['page'],
    'padding': '10px 12px 0 12px',
    'minHeight': '100vh',
}

PAGE_CHROME_STYLE = {
    'position': 'sticky',
    'top': 'var(--cdx-nav-offset, 0px)',
    'zIndex': '900',
    'backgroundColor': THEME['page'],
    'padding': '10px 12px 0 12px',
    'boxShadow': '0 10px 18px rgba(15, 35, 55, 0.05)',
}

PAGE_CONTENT_STYLE = {
    'backgroundColor': THEME['page'],
    'padding': '0 12px 0 12px',
    'width': '100%', 'minWidth': '0', 'boxSizing': 'border-box',
}

CONTROL_SURFACE_STYLE = {
    'backgroundColor': THEME['surface'],
    'border': f'1px solid {THEME["border"]}',
    'borderRadius': '12px',
    'boxShadow': THEME['shadow_soft'],
    'padding': '10px 12px',
    'marginBottom': '10px',
}

CONTROL_ROW_STYLE = {
    'display': 'flex',
    'flexWrap': 'wrap',
    'alignItems': 'center',
    'gap': '8px 12px',
}

CONTROL_GROUP_STYLE = {
    'display': 'flex',
    'flexDirection': 'column',
    'alignItems': 'flex-start',
    'gap': '4px',
    'minWidth': '0', 'maxWidth': '100%',
    'marginRight': '6px',
}

CONTROL_LABEL_STYLE = {
    'fontSize': '11px',
    'color': THEME['muted'],
    'fontWeight': '700',
    'textTransform': 'uppercase',
    'letterSpacing': '0.35px',
    'whiteSpace': 'nowrap',
}

INPUT_STYLE = {
    'fontSize': '12px',
    'padding': '4px 7px',
    'border': f'1px solid {THEME["border_strong"]}',
    'borderRadius': '6px',
    'backgroundColor': THEME['surface'],
    'color': THEME['ink'],
    'boxShadow': 'inset 0 1px 2px rgba(15, 35, 55, 0.04)',
}

SMALL_INPUT_STYLE = {**INPUT_STYLE, 'padding': '3px 6px'}
DROPDOWN_STYLE = {'fontSize': '12px', 'maxWidth': '100%'}
SELECT_SMALL_STYLE = {'fontSize': '12px', 'maxWidth': '100%'}

ACTION_BUTTON_STYLE = {
    'fontSize': '10px',
    'fontWeight': '700',
    'padding': '5px 10px',
    'border': f'1px solid {THEME["border_strong"]}',
    'borderRadius': '7px',
    'backgroundColor': THEME['surface_soft'],
    'color': THEME['ink'],
    'cursor': 'pointer',
    'boxShadow': THEME['shadow_soft'],
}

STATUS_ROW_STYLE = {
    'display': 'flex',
    'flexWrap': 'wrap',
    'alignItems': 'center',
    'gap': '7px',
    'padding': '7px 9px',
    'backgroundColor': THEME['surface_soft'],
    'borderRadius': '9px',
    'border': f'1px solid {THEME["border"]}',
    'marginTop': '8px',
    'minHeight': '30px',
}

STATUS_DOT_STYLE = {
    'fontSize': '11px',
    'color': THEME['faint'],
    'cursor': 'default',
    'lineHeight': '1',
}

STATUS_TEXT_STYLE = {
    'fontSize': '10px',
    'fontFamily': 'Consolas, "Courier New", monospace',
    'whiteSpace': 'nowrap',
    'color': THEME['muted'],
}

STATUS_BADGE_STYLE = {
    **STATUS_TEXT_STYLE,
    'display': 'inline-flex',
    'alignItems': 'center',
    'padding': '2px 7px',
    'borderRadius': '999px',
    'border': f'1px solid {THEME["border"]}',
    'backgroundColor': THEME['surface'],
    'fontWeight': '700',
}

OVERRIDE_ROW_STYLE = {
    'display': 'flex',
    'flexWrap': 'wrap',
    'alignItems': 'center',
    'gap': '8px 14px',
    'marginBottom': '10px',
    'padding': '9px 11px',
    'backgroundColor': THEME['surface'],
    'border': f'1px solid {THEME["border"]}',
    'borderRadius': '10px',
    'boxShadow': THEME['shadow_soft'],
}

TAB_BAR_STYLE = {
    'border': '0',
    'backgroundColor': 'transparent',
    'marginBottom': '10px',
}

TAB_STYLE = {
    'padding': '8px 13px',
    'fontSize': '11px',
    'fontWeight': '700',
    'color': THEME['muted'],
    'backgroundColor': THEME['surface_soft'],
    'border': f'1px solid {THEME["border"]}',
    'borderRadius': '9px 9px 0 0',
}

TAB_SELECTED_STYLE = {
    **TAB_STYLE,
    'color': THEME['accent'],
    'backgroundColor': THEME['surface'],
    'borderTop': f'3px solid {THEME["accent"]}',
    'borderBottom': f'1px solid {THEME["surface"]}',
}

PANEL_CARD_STYLE = {
    'backgroundColor': THEME['surface'],
    'border': f'1px solid {THEME["border"]}',
    'borderRadius': '12px',
    'boxShadow': THEME['shadow_soft'],
    'minWidth': '0', 'maxWidth': '100%', 'boxSizing': 'border-box',
    'padding': '10px',
    'marginBottom': '12px',
}

PANEL_BODY_STYLE = {
    'display': 'block',
}

GRAPH_CARD_STYLE = {
    'backgroundColor': THEME['surface'],
    'border': f'1px solid {THEME["border"]}',
    'borderRadius': '10px',
    'boxShadow': THEME['shadow_soft'],
    'padding': '9px',
    'marginBottom': '10px',
    'minWidth': '0', 'maxWidth': '100%', 'boxSizing': 'border-box',
}

GRAPH_GRID_STYLE = {
    'display': 'grid',
    'gridTemplateColumns': 'repeat(auto-fit, minmax(min(100%, 560px), 1fr))',
    'gap': '14px', 'alignItems': 'start', 'minWidth': '0',
}

GRAPH_STYLE = {'height': '440px', 'width': '100%', 'minWidth': '0'}
GRAPH_CONFIG = {
    'displaylogo': False, 'displayModeBar': 'hover', 'scrollZoom': False,
    'modeBarButtonsToRemove': ['lasso2d', 'select2d', 'autoScale2d'],
    'toImageButtonOptions': {'format': 'png', 'filename': 'cdx-chart', 'scale': 2},
}

LIGHT_TABLE_CELL = {
    'textAlign': 'center',
    'padding': '4px 6px',
    'fontSize': '11px',
    'fontFamily': 'Consolas, "Courier New", monospace',
    'border': f'1px solid {THEME["border"]}',
    'whiteSpace': 'nowrap',
    'overflow': 'hidden',
    'textOverflow': 'ellipsis',
}

LIGHT_TABLE_HEADER = {
    'fontWeight': '800',
    'backgroundColor': '#25364a',
    'color': '#f7fbff',
    'fontSize': '10px',
    'borderBottom': '2px solid #142235',
    'textTransform': 'uppercase',
    'letterSpacing': '0.35px',
    'padding': '5px 6px',
    'fontFamily': 'Consolas, "Courier New", monospace',
}

LIGHT_TABLE_WRAP = {
    'overflowX': 'auto',
    'width': '100%',
    'tableLayout': 'fixed',
    'border': f'1px solid {THEME["border"]}',
    'borderRadius': '9px',
    'boxShadow': THEME['shadow_soft'],
}

BLOTTER_FILTER_STYLE = {
    **INPUT_STYLE,
    'width': '390px',
    'maxWidth': '100%', 'boxSizing': 'border-box',
    'fontFamily': 'Consolas, "Courier New", monospace',
}

EMPTY_BANNER_STYLE = {
    'padding': '10px',
    'color': THEME['muted'],
    'fontSize': '11px',
    'backgroundColor': THEME['surface_soft'],
    'border': f'1px dashed {THEME["border_strong"]}',
    'borderRadius': '8px',
}

OPTION_TABLE_CELL = {
    'textAlign': 'center',
    'padding': '4px 5px',
    'fontSize': '12px',
    'fontFamily': 'Consolas, Monaco, monospace',
    'border': '1px solid #263241',
}

OPTION_TABLE_HEADER = {
    'fontWeight': '800',
    'backgroundColor': '#172033',
    'color': '#f7fbff',
    'fontSize': '10px',
    'textTransform': 'uppercase',
    'letterSpacing': '0.5px',
    'padding': '5px 5px',
    'borderBottom': '2px solid #3b4a5d',
}

OPTION_TABLE_DATA = {
    'backgroundColor': '#101826',
    'color': '#dbe6f3',
    'borderBottom': '1px solid #263241',
}

OPTION_TABLE_WRAP = {
    'overflowX': 'auto',
    'border': '1px solid #304052',
    'borderRadius': '9px',
    'boxShadow': '0 10px 24px rgba(8, 18, 32, 0.16)',
}

OPTION_TABLE_FILTER = {
    'fontSize': '10px',
    'backgroundColor': '#172033',
    'color': '#dbe6f3',
}


FLEX_ROW = {'display': 'flex', 'flexWrap': 'wrap', 'alignItems': 'center'}
FLEX_ITEM = {'display': 'flex', 'alignItems': 'center', 'marginRight': '18px'}
LABEL_STYLE = {'fontSize': '11px', 'color': '#555', 'marginRight': '6px', 'fontWeight': '600'}
SECTION_BORDER = {'borderBottom': '1px solid #e0e0e0', 'paddingBottom': '8px', 'marginBottom': '8px'}
MUTED = {'fontSize': '10px', 'color': '#999'}

STRIP_STYLE = {
    'display': 'flex', 'flexWrap': 'wrap', 'gap': '2px',
    'padding': '6px 10px', 'marginBottom': '8px',
    'backgroundColor': THEME['surface_soft'], 'borderRadius': '9px',
    'border': f'1px solid {THEME["border"]}', 'fontFamily': 'Consolas, Monaco, monospace',
}

BTN_STYLE = {
    'fontSize': '10px', 'padding': '3px 10px', 'border': '1px solid #ccc',
    'borderRadius': '3px', 'backgroundColor': '#fafafa', 'color': '#555',
    'cursor': 'pointer', 'marginRight': '10px',
}

SUMMARY_HEADER_STYLE = {
    'fontWeight': '700', 'backgroundColor': '#2c3e50', 'color': '#ecf0f1',
    'fontSize': '10px', 'borderBottom': '2px solid #1a252f',
    'fontFamily': 'Consolas, "Courier New", monospace',
    'textTransform': 'uppercase', 'letterSpacing': '0.3px', 'padding': '4px 6px',
}

SUMMARY_CELL_STYLE = {
    'textAlign': 'center', 'padding': '3px 6px', 'fontSize': '11px',
    'fontFamily': 'Consolas, "Courier New", monospace',
    'border': '1px solid #e0e0e0',
}

SECTION_HEADER = {
    'fontWeight': '800', 'fontSize': '12px', 'color': THEME['ink'],
    'marginBottom': '6px', 'fontFamily': 'system-ui, -apple-system, sans-serif',
    'letterSpacing': '0.1px',
}

GRID_HEADER = {
    'fontWeight': '800', 'fontSize': '12px', 'color': THEME['ink'],
    'marginBottom': '3px', 'lineHeight': '1.3',
}

GRID_SUBTITLE = {
    'fontSize': '11px', 'color': THEME['muted'], 'fontWeight': '600',
}

DARK_HEADER = {
    'fontWeight': '800', 'fontSize': '14px', 'color': '#f0f6fc',
}

DARK_HEADER_BADGE = {
    'fontSize': '12px', 'fontWeight': '500',
}

DARK_HEADER_ROW = {
    'marginBottom': '6px', 'padding': '6px 0', 'borderBottom': '1px solid #21262d',
    'display': 'flex', 'alignItems': 'center',
}
