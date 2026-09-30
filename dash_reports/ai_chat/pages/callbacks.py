import dash
from dash import Input, Output, State, callback, html, no_update
import re
import json
from datetime import datetime
import logging

from ..data.request_manager import request_manager
from genai.sql_genie.example_questions import get_example_questions


@callback(
    [Output('model-label', 'style'),
     Output('model-selector', 'style'),
     Output('genie-config-store', 'data')],
    [Input('genie-selector', 'value'),
     Input('model-selector', 'value')],
    [State('genie-config-store', 'data')],
    prevent_initial_call=False
)
def update_genie_config(genie_type, model, current_config):
    new_config = current_config.copy() if current_config else {}
    new_config['genie_type'] = genie_type or 'custom'
    new_config['model'] = model

    if genie_type in ['custom', 'agent']:
        model_label_style = {'marginRight': '5px', 'fontSize': '14px', 'display': 'inline-block'}
        model_selector_style = {'width': '130px', 'display': 'inline-block'}
    else:
        model_label_style = {'marginRight': '5px', 'fontSize': '14px', 'display': 'none'}
        model_selector_style = {'width': '130px', 'display': 'none'}

    return model_label_style, model_selector_style, new_config


@callback(
    Output('chat-status', 'children'),
    Output('genie-client-store', 'data'),
    [Input('status-interval', 'n_intervals'),
     Input('genie-config-store', 'data')]
)
def update_status(n_intervals, genie_config):
    _ = n_intervals
    genie_type = genie_config.get('genie_type', 'custom') if genie_config else 'custom'
    genie_names = {
        'databricks': 'Databricks MAS',
        'custom': 'Custom Genie',
        'agent': 'Custom Agent 🚀',
        'glean': 'Glean AI',
        'mcp': 'MCP Toolbox'
    }
    genie_name = genie_names.get(genie_type, 'Custom Genie')

    try:
        health = request_manager.health_check()
        active = health.get('active_requests', 0)
        if active > 0:
            status = f"🔄 {genie_name} Processing {active} request(s)"
        else:
            status = f"✅ {genie_name} Ready"
        return status, {'initialized': True, 'genie_type': genie_type}
    except Exception:
        return f"⚠️ {genie_name} - Teams server offline", {'initialized': True, 'genie_type': genie_type}


@callback(
    [Output('chat-messages', 'children'),
     Output('chat-input', 'value'),
     Output('conversation-store', 'data')],
    [Input('send-button', 'n_clicks'),
     Input('clear-button', 'n_clicks'),
     Input('refresh-button', 'n_clicks'),
     Input('chat-input', 'n_submit')],
    [State('chat-input', 'value'),
     State('conversation-store', 'data'),
     State('genie-client-store', 'data'),
     State('genie-config-store', 'data')],
    prevent_initial_call=False
)
def handle_chat_interaction(send_clicks, clear_clicks, refresh_clicks, input_submit, input_value, conversation_data, client_data, genie_config):
    _ = send_clicks, input_submit
    
    ctx = dash.callback_context
    if not ctx.triggered:
        return no_update, no_update, no_update
    
    trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]
    
    if trigger_id == 'clear-button' and clear_clicks > 0:
        try:
            from flask import session as flask_session
            user_id = flask_session.get('user_id') or flask_session.get('email') or 'anonymous'
        except Exception:
            user_id = 'anonymous'

        session_id = f"dash_chat_{user_id}"
        try:
            genie_config = genie_config or {}
            request_manager.clear_conversation(
                session_id,
                user_id=user_id,
                genie_type=genie_config.get('genie_type'),
                model=genie_config.get('model'),
            )
            logging.info("Cleared conversation for user=%s", user_id)
        except Exception as e:
            logging.warning("Could not clear conversation: %s", e)

        return [create_welcome_message()], '', []
    
    if trigger_id == 'refresh-button' and refresh_clicks > 0:
        updated_conversation = conversation_data.copy()
        conversation_updated = False

        for i, message in enumerate(updated_conversation):
            msg_str = str(message)
            if 'request-id:' in msg_str:
                match = re.search(r'request-id:([a-f0-9-]+)', msg_str)
                if match:
                    request_id = match.group(1)
                    status = request_manager.get_request_status(request_id)
                    if status:
                        if status['status'] == 'completed':
                            updated_conversation[i] = create_message_bubble("🧞 Risk Intern", status['response'], is_genie=True, duration=status.get('duration'))
                            conversation_updated = True
                        elif status['status'] == 'error':
                            updated_conversation[i] = create_message_bubble("System", f"❌ Error: {status['error']}", is_error=True)
                            conversation_updated = True

        return (updated_conversation, no_update, updated_conversation) if conversation_updated else (no_update, no_update, no_update)
    
    if trigger_id in ['send-button', 'chat-input'] and input_value and input_value.strip():
        if not client_data.get('initialized', False):
            error_msg = create_message_bubble("System", "❌ Chat service is not connected. Please wait for connection to be established.", is_error=True)
            return conversation_data + [error_msg], '', conversation_data

        user_message = create_message_bubble("You", input_value, is_user=True)
        
        try:
            from flask import session as flask_session
            user_id = flask_session.get('user_id') or flask_session.get('email') or 'anonymous'
        except Exception:
            user_id = 'anonymous'

        session_id = f"dash_chat_{user_id}"
        genie_config = genie_config or {}
        request_id = request_manager.submit_request(
            input_value, session_id=session_id, user_id=user_id,
            genie_type=genie_config.get('genie_type'), model=genie_config.get('model'),
        )
        processing_message = create_processing_bubble(request_id)
        
        updated_conversation = conversation_data + [user_message, processing_message]
        return updated_conversation, '', updated_conversation
    
    return no_update, no_update, no_update


@callback(
    Output('request-polling-interval', 'disabled'),
    Input('conversation-store', 'data'),
    prevent_initial_call=True
)
def control_polling(conversation_data):
    """Enable polling when there are processing bubbles in the UI"""
    if not conversation_data:
        logging.info("⏸️ Polling disabled - no conversation data")
        return True
    
    # Simple check: if there are any processing bubbles (request-id: in the UI), keep polling
    has_processing_bubbles = False
    processing_request_ids = []
    
    for i, msg in enumerate(conversation_data):
        msg_str = str(msg)
        if 'request-id:' in msg_str:
            import re
            match = re.search(r'request-id:([a-f0-9-]+)', msg_str)
            if match:
                request_id = match.group(1)
                processing_request_ids.append(request_id[:8])
                has_processing_bubbles = True
    
    # Log polling state
    if has_processing_bubbles:
        logging.info(f"🔄 Polling enabled - processing bubbles found: {processing_request_ids}")
    else:
        logging.info("⏸️ Polling disabled - no processing bubbles in UI")
    
    # Keep polling as long as there are processing bubbles in the UI
    return not has_processing_bubbles


@callback(
    Output('chat-messages', 'children', allow_duplicate=True),
    Output('conversation-store', 'data', allow_duplicate=True),
    Input('request-polling-interval', 'n_intervals'),
    State('conversation-store', 'data'),
    State('request-polling-interval', 'disabled'),
    prevent_initial_call=True
)
def poll_request_status(n_intervals, conversation_data, polling_disabled):
    if polling_disabled or not conversation_data:
        return no_update, no_update
    
    logging.info(f"🔄 Polling requests (interval #{n_intervals})")
    
    updated_conversation = conversation_data.copy()
    conversation_updated = False
    
    for i, message in enumerate(updated_conversation):
        msg_str = str(message)
        if 'request-id:' in msg_str:
            match = re.search(r'request-id:([a-f0-9-]+)', msg_str)
            if match:
                request_id = match.group(1)
                status = request_manager.get_request_status(request_id)
                
                logging.info(f"📊 Request {request_id[:8]} status: {status['status'] if status else 'NOT_FOUND'}")
                
                if status:
                    if status['status'] == 'completed':
                        logging.info(f"✅ Request {request_id[:8]} completed, updating UI")
                        updated_conversation[i] = create_message_bubble("🧞 Risk Intern", status['response'], is_genie=True, duration=status.get('duration'))
                        conversation_updated = True
                    elif status['status'] == 'error':
                        logging.info(f"❌ Request {request_id[:8]} failed, updating UI")
                        updated_conversation[i] = create_message_bubble("System", f"❌ Error: {status['error']}", is_error=True)
                        conversation_updated = True
                    elif status['status'] == 'processing':
                        progress = status.get('progress', 0)
                        duration = status.get('duration', 0)
                        logging.info(f"🔄 Request {request_id[:8]} processing ({progress}%, {duration:.1f}s)")
                        updated_conversation[i] = create_processing_bubble(request_id, progress, duration)
                        conversation_updated = True
                else:
                    logging.warning(f"⚠️ Request {request_id[:8]} not found in request manager")
    
    if conversation_updated:
        logging.info("🔄 UI updated from polling")
    else:
        logging.info("🔄 No UI updates needed")
    
    return (updated_conversation, updated_conversation) if conversation_updated else (no_update, no_update)


@callback(
    Output('chat-messages', 'children', allow_duplicate=True),
    Output('conversation-store', 'data', allow_duplicate=True),
    Input({'type': 'cancel-request', 'index': dash.dependencies.ALL}, 'n_clicks'),
    State('conversation-store', 'data'),
    prevent_initial_call=True
)
def handle_cancel_request(n_clicks_list, conversation_data):
    ctx = dash.callback_context
    if not ctx.triggered or not any(n_clicks_list):
        return no_update, no_update
    
    triggered_id = ctx.triggered[0]['prop_id']
    button_id = json.loads(triggered_id.split('.')[0])
    request_id = button_id['index']
    
    if request_manager.cancel_request(request_id):
        updated_conversation = conversation_data.copy()
        
        for i, message in enumerate(updated_conversation):
            if hasattr(message, 'children') and f'request-id:{request_id}' in str(message.children):
                updated_conversation[i] = create_message_bubble("System", "❌ Request cancelled by user", is_error=True)
                break
        
        return updated_conversation, updated_conversation
    
    return no_update, no_update


def create_welcome_message():
    """Create the welcome message bubble"""
    return html.Div([
        html.Div([
            html.Div("🧞", style={'fontSize': '24px', 'marginRight': '10px'}),
            html.Div([
                html.Strong("Risk Intern"),
                html.Div("Just now", style={'fontSize': '12px', 'color': '#666'})
            ])
        ], style={'display': 'flex', 'alignItems': 'center', 'marginBottom': '10px'}),
        
        html.Div([
            html.P("👋 Welcome! I'm here to help with APO positions, risk and PnL analysis."),
            html.P("Ask me naturally about:"),
            html.Ul([html.Li(f'"{q}"') for q in get_example_questions()]),
            html.P("Just type your question and I'll analyze the data for you! 📊")
        ], style={'backgroundColor': '#f0f8ff', 'padding': '15px', 'borderRadius': '8px', 'border': '1px solid #e1e5e9'})
    ], className='chat-message', style={'marginBottom': '20px'})


def create_message_bubble(sender, message, is_user=False, is_genie=False, is_error=False, duration=None):
    """Create a message bubble for the chat"""
    # Parameter is_genie reserved for future styling differences
    _ = is_genie  # Mark as used
    
    timestamp = datetime.now().strftime("%H:%M")
    
    if is_user:
        bubble_style = {
            'backgroundColor': '#6264a7',
            'color': 'white',
            'marginLeft': 'auto',
            'marginRight': '0',
            'maxWidth': '70%',
            'padding': '12px 16px',
            'borderRadius': '18px 18px 4px 18px',
            'marginBottom': '10px',
            'wordWrap': 'break-word'
        }
        
        container_style = {
            'display': 'flex',
            'justifyContent': 'flex-end',
            'marginBottom': '15px'
        }
        
        return html.Div([
            html.Div([
                html.Div(message, style={'marginBottom': '5px'}),
                html.Div(timestamp, style={'fontSize': '11px', 'opacity': '0.8'})
            ], style=bubble_style)
        ], style=container_style)
    
    elif is_error:
        bubble_style = {
            'backgroundColor': '#fee',
            'color': '#c53030',
            'border': '1px solid #feb2b2',
            'padding': '12px 16px',
            'borderRadius': '8px',
            'marginBottom': '15px',
            'wordWrap': 'break-word'
        }
        
        return html.Div([
            html.Div([
                html.Strong(f"{sender} • {timestamp}"),
                html.Div(message, style={'marginTop': '5px'})
            ], style=bubble_style)
        ])
    
    else:  # Genie or system message
        bubble_style = {
            'backgroundColor': '#f8f9fa',
            'border': '1px solid #e9ecef',
            'padding': '15px',
            'borderRadius': '8px',
            'marginBottom': '15px',
            'wordWrap': 'break-word'
        }
        
        duration_badge = None
        if duration is not None and duration > 0:
            duration_badge = html.Span(
                f"⚡ {duration:.1f}s",
                style={
                    'fontSize': '11px',
                    'color': '#16a34a',
                    'backgroundColor': '#dcfce7',
                    'padding': '2px 8px',
                    'borderRadius': '10px',
                    'marginLeft': '10px',
                    'fontWeight': '600'
                }
            )
        
        return html.Div([
            html.Div([
                html.Div([
                    html.Strong(sender, style={'color': '#2c3e50'}),
                    html.Span(f" • {timestamp}", style={'fontSize': '12px', 'color': '#7f8c8d', 'marginLeft': '10px'}),
                    duration_badge
                ], style={'marginBottom': '12px', 'borderBottom': '1px solid #ecf0f1', 'paddingBottom': '8px'}),
                html.Div(
                    # Parse the message for better formatting
                    parse_genie_response(message),
                    style={
                        'lineHeight': '1.6',
                        'color': '#2c3e50'
                    }
                )
            ], style=bubble_style)
        ])


def create_processing_bubble(request_id, progress=0, duration=0):
    """Create a processing indicator with progress"""
    
    # Create progress bar
    progress_bar = html.Div([
        html.Div(
            style={
                'width': f'{progress}%',
                'height': '4px',
                'backgroundColor': '#6264a7',
                'borderRadius': '2px',
                'transition': 'width 0.3s ease'
            }
        )
    ], style={
        'width': '100%',
        'height': '4px',
        'backgroundColor': '#e9ecef',
        'borderRadius': '2px',
        'marginTop': '8px'
    })
    
    # Status text
    if progress == 0:
        status_text = "Queued..."
    elif progress < 30:
        status_text = "Initializing..."
    elif progress < 70:
        status_text = "Processing query..."
    else:
        status_text = "Finalizing response..."
    
    duration_text = f" ({duration:.1f}s)" if duration > 0 else ""
    
    return html.Div([
        html.Div([
            html.Div("🧞", style={'fontSize': '20px', 'marginRight': '10px'}),
            html.Div([
                html.Strong("Risk Intern is working"),
                html.Div(f"{status_text}{duration_text}", 
                        style={'fontSize': '14px', 'color': '#666', 'fontStyle': 'italic'}),
                progress_bar
            ], style={'flex': '1'}),
            html.Button(
                "Cancel",
                id={'type': 'cancel-request', 'index': request_id},
                n_clicks=0,
                style={
                    'padding': '4px 12px',
                    'backgroundColor': '#dc3545',
                    'color': 'white',
                    'border': 'none',
                    'borderRadius': '4px',
                    'fontSize': '12px',
                    'cursor': 'pointer',
                    'marginLeft': '10px'
                }
            )
        ], style={
            'display': 'flex', 
            'alignItems': 'center',
            'backgroundColor': '#f8f9fa',
            'border': '1px solid #e9ecef',
            'padding': '12px 16px',
            'borderRadius': '8px',
            'marginBottom': '15px',
            'opacity': '0.9'
        }),
        # Hidden request ID for polling
        html.Div(f"request-id:{request_id}", style={'display': 'none'})
    ])


def create_thinking_bubble():
    """Create a simple thinking/typing indicator (legacy)"""
    return html.Div([
        html.Div([
            html.Div("🧞", style={'fontSize': '20px', 'marginRight': '10px'}),
            html.Div([
                html.Strong("Risk Intern is thinking"),
                html.Div("...", style={'fontSize': '14px', 'color': '#666', 'fontStyle': 'italic'})
            ])
        ], style={
            'display': 'flex', 
            'alignItems': 'center',
            'backgroundColor': '#f8f9fa',
            'border': '1px solid #e9ecef',
            'padding': '12px 16px',
            'borderRadius': '8px',
            'marginBottom': '15px',
            'opacity': '0.8'
        })
    ])


def _fix_malformed_tables(message):
    """Fix tables that LLM reformatted - extract pipe-separated data and reconstruct proper markdown tables"""
    import re
    lines = message.split('\n')
    fixed_lines = []
    i = 0
    
    while i < len(lines):
        line = lines[i].strip()
        
        if 'DATA RESULTS' in line:
            fixed_lines.append(lines[i])
            i += 1
            
            while i < len(lines) and not lines[i].strip():
                fixed_lines.append(lines[i])
                i += 1
            
            if i < len(lines):
                next_line = lines[i].strip()
                if '|' in next_line and (next_line.startswith('*') or next_line.startswith('_')):
                    header_line = next_line
                    header_line = re.sub(r'^\*+|\*+$|^_+|_+$', '', header_line).strip()
                    headers = [h.strip() for h in header_line.split('|') if h.strip()]
                    
                    if len(headers) >= 2:
                        proper_table = []
                        proper_table.append('| ' + ' | '.join(headers) + ' |')
                        proper_table.append('|' + '|'.join(['---' for _ in headers]) + '|')
                        
                        i += 1
                        
                        while i < len(lines):
                            row_line = lines[i].strip()
                            
                            if not row_line:
                                i += 1
                                if i < len(lines) and '|' not in lines[i]:
                                    break
                                continue
                            
                            if '|' not in row_line:
                                break
                            
                            if 'KEY INSIGHTS' in row_line or 'ISSUES' in row_line or row_line.startswith('#'):
                                break
                            
                            cells = [c.strip() for c in row_line.split('|') if c.strip()]
                            
                            while len(cells) < len(headers):
                                cells.append('')
                            
                            proper_table.append('| ' + ' | '.join(cells[:len(headers)]) + ' |')
                            i += 1
                        
                        fixed_lines.extend(proper_table)
                        continue
        
        if '|' in line and line.count('|') >= 2 and 'DATA RESULTS' not in line:
            table_lines = []
            start_i = i
            
            while i < len(lines) and '|' in lines[i]:
                table_lines.append(lines[i].strip())
                i += 1
            
            if len(table_lines) >= 2:
                first_line = table_lines[0]
                headers = [h.strip() for h in re.split(r'\|', first_line) if h.strip()]
                
                if len(headers) >= 2:
                    proper_table = []
                    clean_headers = []
                    for h in headers:
                        h = re.sub(r'^\*+|\*+$|^_+|_+$', '', h).strip()
                        clean_headers.append(h)
                    
                    proper_table.append('| ' + ' | '.join(clean_headers) + ' |')
                    proper_table.append('|' + '|'.join(['---' for _ in clean_headers]) + '|')
                    
                    for row_line in table_lines[1:]:
                        if '---' in row_line or '===' in row_line:
                            continue
                        
                        cells = [c.strip() for c in re.split(r'\|', row_line) if c.strip()]
                        
                        while len(cells) < len(clean_headers):
                            cells.append('')
                        
                        proper_table.append('| ' + ' | '.join(cells[:len(clean_headers)]) + ' |')
                    
                    fixed_lines.extend(proper_table)
                    continue
            
            i = start_i
        
        fixed_lines.append(lines[i] if i < len(lines) else '')
        i += 1
    
    return '\n'.join(fixed_lines)


def parse_genie_response(message):
    """Parse genie response with comprehensive HTML markup formatting"""
    if not message:
        return message
    
    import re
    
    message = _fix_malformed_tables(message)
    
    # Split message into lines for processing
    lines = message.split('\n')
    formatted_elements = []
    i = 0
    
    while i < len(lines):
        line = lines[i]
        stripped_line = line.strip()
        
        # Skip empty lines but add spacing
        if not stripped_line:
            formatted_elements.append(html.Br())
            i += 1
            continue
        
        # Handle code blocks (```language or ```)
        if stripped_line.startswith('```'):
            code_block, i = _parse_code_block(lines, i)
            formatted_elements.append(code_block)
            continue
        
        # Handle headers (# ## ### #### ##### ######)
        if stripped_line.startswith('#'):
            header_element = _parse_header(stripped_line)
            formatted_elements.append(header_element)
            i += 1
            continue
        
        # Handle tables (lines with | separators)
        if '|' in stripped_line and _is_table_row(stripped_line):
            table_element, i = _parse_table(lines, i)
            formatted_elements.append(table_element)
            continue
        
        # Handle lists (- * + or numbered 1. 2. etc.)
        if re.match(r'^[\s]*[-*+]\s+', stripped_line) or re.match(r'^[\s]*\d+\.\s+', stripped_line):
            list_element, i = _parse_list(lines, i)
            formatted_elements.append(list_element)
            continue
        
        # Handle blockquotes (> text)
        if stripped_line.startswith('>'):
            quote_element = _parse_blockquote(stripped_line)
            formatted_elements.append(quote_element)
            i += 1
            continue
        
        # Handle horizontal rules (--- or ***)
        if stripped_line in ['---', '***', '___']:
            formatted_elements.append(html.Hr(style={
                'margin': '20px 0',
                'border': 'none',
                'borderTop': '1px solid #e1e5e9'
            }))
            i += 1
            continue
        
        # Handle regular paragraphs with inline formatting
        paragraph_element = _parse_paragraph_with_inline_formatting(stripped_line)
        formatted_elements.append(paragraph_element)
        i += 1
    
    return formatted_elements


def _parse_code_block(lines, start_idx):
    start_line = lines[start_idx].strip()
    language = start_line[3:].strip() if len(start_line) > 3 else ''
    
    code_lines = []
    i = start_idx + 1
    
    # Find the closing ```
    while i < len(lines):
        if lines[i].strip() == '```':
            break
        code_lines.append(lines[i])
        i += 1
    
    code_content = '\n'.join(code_lines)
    
    # Style based on language
    code_style = {
        'backgroundColor': '#f8f9fa',
        'border': '1px solid #e9ecef',
        'borderRadius': '6px',
        'padding': '16px',
        'fontFamily': 'Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
        'fontSize': '14px',
        'lineHeight': '1.45',
        'overflow': 'auto',
        'whiteSpace': 'pre',
        'margin': '16px 0'
    }
    
    # Add language-specific styling and basic syntax highlighting
    language_lower = language.lower()
    if language_lower in ['sql', 'python', 'javascript', 'json', 'yaml', 'html', 'css', 'bash', 'shell']:
        code_style['backgroundColor'] = '#f6f8fa'
        code_style['border'] = '1px solid #d1d9e0'
        
        # Apply basic syntax highlighting
        code_content = _apply_basic_syntax_highlighting(code_content, language_lower)
    
    language_label = None
    if language:
        language_label = html.Div(
            language.upper(),
            style={
                'fontSize': '12px',
                'color': '#586069',
                'marginBottom': '8px',
                'fontWeight': 'bold'
            }
        )
    
    # Create code element - handle HTML content if syntax highlighting was applied
    if '<span' in code_content:
        # HTML content from syntax highlighting
        code_element = html.Pre(
            html.Code(
                children=[],  # Empty children
                dangerouslySetInnerHTML={'__html': code_content}
            ),
            style=code_style
        )
    else:
        # Plain text content
        code_element = html.Pre(
            html.Code(code_content),
            style=code_style
        )
    
    code_block = html.Div([
        language_label,
        code_element
    ], style={'margin': '16px 0'})
    
    return code_block, i + 1


def _parse_header(line):
    match = re.match(r'^(#{1,6})\s+(.+)', line.strip())
    if not match:
        return html.P(line)
    
    level = len(match.group(1))
    text = match.group(2)
    
    header_styles = {
        1: {'fontSize': '28px', 'fontWeight': 'bold', 'margin': '24px 0 16px 0', 'color': '#1a1a1a'},
        2: {'fontSize': '24px', 'fontWeight': 'bold', 'margin': '20px 0 12px 0', 'color': '#1a1a1a'},
        3: {'fontSize': '20px', 'fontWeight': 'bold', 'margin': '16px 0 8px 0', 'color': '#2a2a2a'},
        4: {'fontSize': '18px', 'fontWeight': 'bold', 'margin': '12px 0 6px 0', 'color': '#2a2a2a'},
        5: {'fontSize': '16px', 'fontWeight': 'bold', 'margin': '8px 0 4px 0', 'color': '#3a3a3a'},
        6: {'fontSize': '14px', 'fontWeight': 'bold', 'margin': '6px 0 2px 0', 'color': '#3a3a3a'}
    }
    
    header_tag = getattr(html, f'H{level}')
    return header_tag(
        _parse_inline_formatting(text),
        style=header_styles.get(level, header_styles[6])
    )


def _is_table_row(line):
    """Check if line looks like a table row"""
    return line.count('|') >= 2


def _parse_table(lines, start_idx):
    """Parse a markdown-style table"""
    table_rows = []
    i = start_idx
    
    # Collect all table rows
    while i < len(lines) and _is_table_row(lines[i].strip()):
        table_rows.append(lines[i].strip())
        i += 1
    
    if len(table_rows) < 2:
        # Not a valid table, return as regular text
        return html.P(lines[start_idx]), start_idx + 1
    
    # Parse header row
    header_cells = [cell.strip() for cell in table_rows[0].split('|')[1:-1]]
    
    # Skip separator row (usually contains --- or similar)
    data_rows = table_rows[2:] if len(table_rows) > 2 else []
    
    # Create table header
    header_row = html.Tr([
        html.Th(
            _parse_inline_formatting(cell),
            style={
                'padding': '12px',
                'backgroundColor': '#f8f9fa',
                'borderBottom': '2px solid #dee2e6',
                'fontWeight': 'bold',
                'textAlign': 'left'
            }
        ) for cell in header_cells
    ])
    
    # Create table body rows
    body_rows = []
    for row in data_rows:
        cells = [cell.strip() for cell in row.split('|')[1:-1]]
        # Pad cells if needed
        while len(cells) < len(header_cells):
            cells.append('')
        
        body_row = html.Tr([
            html.Td(
                _parse_inline_formatting(cell),
                style={
                    'padding': '12px',
                    'borderBottom': '1px solid #dee2e6'
                }
            ) for cell in cells[:len(header_cells)]
        ])
        body_rows.append(body_row)
    
    table = html.Table([
        html.Thead(header_row),
        html.Tbody(body_rows)
    ], style={
        'width': '100%',
        'borderCollapse': 'collapse',
        'border': '1px solid #dee2e6',
        'margin': '16px 0',
        'backgroundColor': 'white'
    })
    
    return table, i


def _parse_list(lines, start_idx):
    
    list_items = []
    i = start_idx
    is_ordered = re.match(r'^[\s]*\d+\.\s+', lines[i].strip())
    
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        
        # Check if this is a list item
        unordered_match = re.match(r'^[\s]*[-*+]\s+(.+)', line)
        ordered_match = re.match(r'^[\s]*\d+\.\s+(.+)', line)
        
        if (is_ordered and ordered_match) or (not is_ordered and unordered_match):
            item_text = ordered_match.group(1) if ordered_match else unordered_match.group(1)
            list_items.append(html.Li(
                _parse_inline_formatting(item_text),
                style={'margin': '4px 0', 'lineHeight': '1.5'}
            ))
            i += 1
        else:
            break
    
    list_style = {
        'margin': '16px 0',
        'paddingLeft': '24px'
    }
    
    if is_ordered:
        return html.Ol(list_items, style=list_style), i
    else:
        return html.Ul(list_items, style=list_style), i


def _parse_blockquote(line):
    """Parse blockquote (> text)"""
    text = line[1:].strip()
    return html.Blockquote(
        _parse_inline_formatting(text),
        style={
            'margin': '16px 0',
            'padding': '12px 16px',
            'borderLeft': '4px solid #dfe2e5',
            'backgroundColor': '#f6f8fa',
            'fontStyle': 'italic',
            'color': '#586069'
        }
    )


def _parse_paragraph_with_inline_formatting(text):
    """Parse a paragraph with inline formatting"""
    return html.P(
        _parse_inline_formatting(text),
        style={
            'margin': '12px 0',
            'lineHeight': '1.6'
        }
    )


def _parse_inline_formatting(text):
    if not text:
        return text
    
    # Split text into segments and format each
    elements = []
    current_pos = 0
    
    # Combined regex for all inline formatting
    patterns = [
        (r'\$\$([^$]+)\$\$', 'math_block'),     # $$math block$$
        (r'\$([^$]+)\$', 'math_inline'),        # $inline math$
        (r'\*\*([^*]+)\*\*', 'bold'),           # **bold**
        (r'\*([^*]+)\*', 'italic'),             # *italic*
        (r'`([^`]+)`', 'code'),                 # `code`
        (r'\[([^\]]+)\]\(([^)]+)\)', 'link'),   # [text](url)
        (r'https?://[^\s]+', 'url'),            # bare URLs
        (r'~~([^~]+)~~', 'strikethrough'),      # ~~strikethrough~~
    ]
    
    # Find all matches
    matches = []
    for pattern, format_type in patterns:
        for match in re.finditer(pattern, text):
            matches.append((match.start(), match.end(), match, format_type))
    
    # Sort matches by position
    matches.sort(key=lambda x: x[0])
    
    # Process matches
    for start, end, match, format_type in matches:
        # Add text before this match
        if start > current_pos:
            elements.append(text[current_pos:start])
        
        # Add formatted element
        if format_type == 'math_block':
            elements.append(_create_math_element(match.group(1), is_block=True))
        elif format_type == 'math_inline':
            elements.append(_create_math_element(match.group(1), is_block=False))
        elif format_type == 'bold':
            elements.append(html.Strong(match.group(1)))
        elif format_type == 'italic':
            elements.append(html.Em(match.group(1)))
        elif format_type == 'code':
            elements.append(html.Code(
                match.group(1),
                style={
                    'backgroundColor': '#f1f3f4',
                    'padding': '2px 6px',
                    'borderRadius': '3px',
                    'fontSize': '90%',
                    'fontFamily': 'Monaco, Consolas, monospace'
                }
            ))
        elif format_type == 'link':
            elements.append(html.A(
                match.group(1),
                href=match.group(2),
                target='_blank',
                style={'color': '#0366d6', 'textDecoration': 'none'}
            ))
        elif format_type == 'url':
            url = match.group(0)
            elements.append(html.A(
                url,
                href=url,
                target='_blank',
                style={'color': '#0366d6', 'textDecoration': 'none'}
            ))
        elif format_type == 'strikethrough':
            elements.append(html.Del(match.group(1)))
        
        current_pos = end
    
    # Add remaining text
    if current_pos < len(text):
        elements.append(text[current_pos:])
    
    return elements if elements else text


def _create_math_element(math_content, is_block=False):
    
    math_style = {
        'fontFamily': 'KaTeX_Math, Times, serif',
        'fontStyle': 'italic',
        'color': '#333',
        'backgroundColor': '#f9f9f9',
        'padding': '4px 8px' if is_block else '2px 4px',
        'borderRadius': '4px',
        'border': '1px solid #e1e5e9',
        'display': 'inline-block' if is_block else 'inline',
        'margin': '8px 0' if is_block else '0 2px',
        'fontSize': '16px' if is_block else '14px'
    }
    
    if is_block:
        math_style.update({
            'display': 'block',
            'textAlign': 'center',
            'margin': '16px 0',
            'padding': '12px',
            'fontSize': '18px'
        })
    
    # Simple math formatting - replace common symbols
    formatted_math = math_content
    
    # Replace common mathematical symbols with Unicode equivalents
    replacements = {
        'alpha': 'α', 'beta': 'β', 'gamma': 'γ', 'delta': 'δ', 'epsilon': 'ε',
        'theta': 'θ', 'lambda': 'λ', 'mu': 'μ', 'pi': 'π', 'sigma': 'σ',
        'phi': 'φ', 'chi': 'χ', 'psi': 'ψ', 'omega': 'ω',
        'Alpha': 'Α', 'Beta': 'Β', 'Gamma': 'Γ', 'Delta': 'Δ', 'Theta': 'Θ',
        'Lambda': 'Λ', 'Pi': 'Π', 'Sigma': 'Σ', 'Phi': 'Φ', 'Omega': 'Ω',
        'infty': '∞', 'sum': '∑', 'prod': '∏', 'int': '∫',
        'partial': '∂', 'nabla': '∇', 'sqrt': '√',
        'leq': '≤', 'geq': '≥', 'neq': '≠', 'approx': '≈',
        'pm': '±', 'mp': '∓', 'times': '×', 'div': '÷',
        'cdot': '⋅', 'bullet': '•', 'cap': '∩', 'cup': '∪',
        'subset': '⊂', 'supset': '⊃', 'subseteq': '⊆', 'supseteq': '⊇',
        'in': '∈', 'notin': '∉', 'exists': '∃', 'forall': '∀',
        'rightarrow': '→', 'leftarrow': '←', 'leftrightarrow': '↔',
        'Rightarrow': '⇒', 'Leftarrow': '⇐', 'Leftrightarrow': '⇔'
    }
    
    for latex_symbol, unicode_symbol in replacements.items():
        formatted_math = formatted_math.replace(f'\\{latex_symbol}', unicode_symbol)
    
    frac_pattern = r'\\frac\{([^}]+)\}\{([^}]+)\}'
    formatted_math = re.sub(frac_pattern, r'(\1)/(\2)', formatted_math)
    
    # Handle superscripts and subscripts (simple approximation)
    # ^{text} -> superscript, _{text} -> subscript
    sup_pattern = r'\^\{([^}]+)\}'
    sub_pattern = r'_\{([^}]+)\}'
    
    # For now, just use parentheses to indicate super/subscripts
    formatted_math = re.sub(sup_pattern, r'^(\1)', formatted_math)
    formatted_math = re.sub(sub_pattern, r'_(\1)', formatted_math)
    
    return html.Span(
        formatted_math,
        style=math_style,
        title=f"Mathematical expression: {math_content}"
    )


def _apply_basic_syntax_highlighting(code_content, language):
    
    # For HTML display, we'll use spans with different colors
    # This is a simplified syntax highlighter
    
    if language == 'sql':
        # SQL keywords
        sql_keywords = [
            'SELECT', 'FROM', 'WHERE', 'JOIN', 'INNER', 'LEFT', 'RIGHT', 'OUTER',
            'GROUP BY', 'ORDER BY', 'HAVING', 'INSERT', 'UPDATE', 'DELETE',
            'CREATE', 'ALTER', 'DROP', 'TABLE', 'INDEX', 'VIEW', 'DATABASE',
            'AS', 'AND', 'OR', 'NOT', 'IN', 'EXISTS', 'BETWEEN', 'LIKE',
            'IS', 'NULL', 'DISTINCT', 'COUNT', 'SUM', 'AVG', 'MAX', 'MIN',
            'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'WITH'
        ]
        
        # Create a pattern that matches whole words only
        for keyword in sql_keywords:
            pattern = r'\b' + re.escape(keyword) + r'\b'
            replacement = f'<span style="color: #0066cc; font-weight: bold;">{keyword}</span>'
            code_content = re.sub(pattern, replacement, code_content, flags=re.IGNORECASE)
        
        # Highlight strings
        code_content = re.sub(r"'([^']*)'", r"<span style='color: #008000;'>'$1'</span>", code_content)
        code_content = re.sub(r'"([^"]*)"', r'<span style="color: #008000;">"$1"</span>', code_content)
        
        # Highlight comments
        code_content = re.sub(r'--([^\n]*)', r'<span style="color: #808080; font-style: italic;">--$1</span>', code_content)
    
    elif language == 'python':
        # Python keywords
        python_keywords = [
            'def', 'class', 'if', 'elif', 'else', 'for', 'while', 'try', 'except',
            'finally', 'with', 'as', 'import', 'from', 'return', 'yield', 'pass',
            'break', 'continue', 'and', 'or', 'not', 'in', 'is', 'lambda',
            'True', 'False', 'None', 'self', 'cls'
        ]
        
        for keyword in python_keywords:
            pattern = r'\b' + re.escape(keyword) + r'\b'
            replacement = f'<span style="color: #0066cc; font-weight: bold;">{keyword}</span>'
            code_content = re.sub(pattern, replacement, code_content)
        
        # Highlight strings
        code_content = re.sub(r"'([^']*)'", r"<span style='color: #008000;'>'$1'</span>", code_content)
        code_content = re.sub(r'"([^"]*)"', r'<span style="color: #008000;">"$1"</span>', code_content)
        code_content = re.sub(r'"""([^"]*)"""', r'<span style="color: #008000;">"""$1"""</span>', code_content, flags=re.DOTALL)
        
        # Highlight comments
        code_content = re.sub(r'#([^\n]*)', r'<span style="color: #808080; font-style: italic;">#$1</span>', code_content)
    
    elif language in ['javascript', 'js']:
        # JavaScript keywords
        js_keywords = [
            'function', 'var', 'let', 'const', 'if', 'else', 'for', 'while',
            'do', 'switch', 'case', 'default', 'break', 'continue', 'return',
            'try', 'catch', 'finally', 'throw', 'new', 'this', 'typeof',
            'instanceof', 'true', 'false', 'null', 'undefined'
        ]
        
        for keyword in js_keywords:
            pattern = r'\b' + re.escape(keyword) + r'\b'
            replacement = f'<span style="color: #0066cc; font-weight: bold;">{keyword}</span>'
            code_content = re.sub(pattern, replacement, code_content)
        
        # Highlight strings
        code_content = re.sub(r"'([^']*)'", r"<span style='color: #008000;'>'$1'</span>", code_content)
        code_content = re.sub(r'"([^"]*)"', r'<span style="color: #008000;">"$1"</span>', code_content)
        code_content = re.sub(r'`([^`]*)`', r'<span style="color: #008000;">`$1`</span>', code_content)
        
        # Highlight comments
        code_content = re.sub(r'//([^\n]*)', r'<span style="color: #808080; font-style: italic;">//$1</span>', code_content)
        code_content = re.sub(r'/\*([^*]*)\*/', r'<span style="color: #808080; font-style: italic;">/*$1*/</span>', code_content, flags=re.DOTALL)
    
    elif language == 'json':
        # Highlight JSON keys and values
        code_content = re.sub(r'"([^"]*)"(\s*:)', r'<span style="color: #0066cc; font-weight: bold;">"$1"</span>$2', code_content)
        code_content = re.sub(r':\s*"([^"]*)"', r': <span style="color: #008000;">"$1"</span>', code_content)
        code_content = re.sub(r'\b(true|false|null)\b', r'<span style="color: #cc6600; font-weight: bold;">$1</span>', code_content)
        code_content = re.sub(r'\b(\d+\.?\d*)\b', r'<span style="color: #cc0066;">$1</span>', code_content)
    
    return code_content
