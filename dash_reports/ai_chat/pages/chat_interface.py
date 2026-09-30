from dash import dcc, html
from genai.sql_genie.example_questions import get_example_questions


def _get_model_options():
    """Dynamically get model options from ModelManager"""
    try:
        from genai.sql_genie.clients.custom_genie import ModelManager
        models = ModelManager.get_available_models()
        return [{'label': model, 'value': model} for model in models]
    except Exception:
        return [{'label': 'gpt-5', 'value': 'gpt-5'}]


def get_chat_headers(app):
    _ = app
    return html.Div([
        html.Div([
            html.Div([
                html.H2("🧞 Risk Intern", className="page-title", style={'margin': '0', 'display': 'inline-block', 'marginRight': '15px'}),
                html.A("📖 Help", href="/assets/help/user_guide.html", target="_blank",
                      style={'fontSize': '14px', 'color': '#006D77', 'textDecoration': 'none', 
                             'padding': '5px 12px', 'border': '1px solid #006D77', 'borderRadius': '4px',
                             'display': 'inline-block', 'fontWeight': '500'})
            ], style={'display': 'flex', 'alignItems': 'center'}),
            html.Div([
                html.Label("Genie:", style={'marginRight': '5px', 'fontSize': '14px'}),
                dcc.Dropdown(
                    id='genie-selector',
                    options=[
                        {'label': 'Custom Genie', 'value': 'custom'},
                        {'label': 'Custom Agent 🚀', 'value': 'agent'},
                        {'label': 'Databricks MAS', 'value': 'databricks'},
                        {'label': 'Glean AI', 'value': 'glean'},
                        {'label': 'MCP Toolbox', 'value': 'mcp'}
                    ],
                    value='custom',
                    style={'width': '170px', 'display': 'inline-block', 'marginRight': '15px'}
                ),
                html.Label("Model:", id='model-label', style={'marginRight': '5px', 'fontSize': '14px', 'display': 'inline-block'}),
                dcc.Dropdown(
                    id='model-selector',
                    options=_get_model_options(),
                    value='openai-gpt-5',
                    style={'width': '380px', 'display': 'inline-block'}
                )
            ], style={'float': 'right', 'display': 'flex', 'alignItems': 'center'})
        ], style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center', 'marginBottom': '10px'}),
        html.P("Ask questions about APO positions, risk and PnL", className="page-subtitle", style={'clear': 'both'})
    ])


def create_chat_layout():
    styles = {
        'container': {
            'height': '600px', 'border': '1px solid #e1e5e9', 'borderRadius': '8px',
            'backgroundColor': '#ffffff', 'display': 'flex', 'flexDirection': 'column',
            'overflow': 'hidden', 'boxShadow': '0 2px 4px rgba(0,0,0,0.1)'
        },
        'messages': {
            'flex': '1', 'overflowY': 'auto', 'padding': '20px', 'backgroundColor': '#fafafa'
        },
        'input_container': {
            'padding': '15px', 'borderTop': '1px solid #e1e5e9', 'backgroundColor': '#ffffff',
            'display': 'flex', 'gap': '10px', 'alignItems': 'center'
        },
        'input': {
            'flex': '1', 'padding': '10px 15px', 'border': '1px solid #d1d5db',
            'borderRadius': '20px', 'fontSize': '14px', 'outline': 'none'
        },
        'button': {
            'padding': '10px 20px', 'backgroundColor': '#6264a7', 'color': 'white',
            'border': 'none', 'borderRadius': '20px', 'cursor': 'pointer',
            'fontSize': '14px', 'fontWeight': 'bold'
        }
    }
    
    welcome_message = html.Div([
        html.Div([
            html.Div("🧞", style={'fontSize': '24px', 'marginRight': '10px'}),
            html.Div([html.Strong("APO Risk Intern"), html.Div("Just now", style={'fontSize': '12px', 'color': '#666'})])
        ], style={'display': 'flex', 'alignItems': 'center', 'marginBottom': '10px'}),
        html.Div([
            html.P("👋 Welcome to your APO Risk Intern!"),
            html.P("I can help you analyze financial data, portfolios, and market insights. Just ask me naturally:"),
            html.Ul([html.Li(f'"{q}"') for q in get_example_questions()]),
            html.P("Type your question below and I'll provide insights with data! 📊")
        ], style={'backgroundColor': '#f0f8ff', 'padding': '15px', 'borderRadius': '8px', 'border': '1px solid #e1e5e9'})
    ], className='chat-message', style={'marginBottom': '20px'})
    
    return html.Div([
        html.Div(id='chat-status', children="🔄 Connecting...", 
                style={'textAlign': 'right', 'color': '#666', 'marginBottom': '10px'}),
        html.Div([
            html.Div(id='chat-messages', children=[welcome_message], style=styles['messages']),
            html.Div([
                dcc.Input(id='chat-input', type='text', 
                         placeholder='Ask about APO positions, risk metrics, PnL drivers...',
                         style=styles['input'], autoComplete='off'),
                html.Button('Send', id='send-button', n_clicks=0, style=styles['button']),
                html.Button('Clear', id='clear-button', n_clicks=0, 
                           style={**styles['button'], 'backgroundColor': '#dc3545', 'marginLeft': '5px'}),
                html.Button('Refresh', id='refresh-button', n_clicks=0, 
                           style={**styles['button'], 'backgroundColor': '#28a745', 'marginLeft': '5px'})
            ], style=styles['input_container'])
        ], style=styles['container']),
        dcc.Store(id='conversation-store', data=[]),
        dcc.Store(id='genie-client-store', data={'initialized': False}),
        dcc.Store(id='genie-config-store', data={'genie_type': 'custom', 'model': 'openai-gpt-5'}),
        dcc.Interval(id='status-interval', interval=10000, n_intervals=0, max_intervals=1),
        dcc.Interval(id='request-polling-interval', interval=5000, n_intervals=0, disabled=True)
    ])


def get_section_specific_layout(section_name):
    _ = section_name
    return create_chat_layout()
