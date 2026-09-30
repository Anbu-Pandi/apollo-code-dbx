import json
import logging
import uuid

import pyarrow.flight as flight

from apo_config.config import required_config
from common.service_discovery import resolve_internal_service_host
from flight_server.client_sdk.flight_transport import connect_flight, load_tls_root
from flight_server.client_sdk.stream_recovery import (
    is_recoverable_stream_error,
    iter_session_recovery,
)

_client = None


def get_client():
    global _client
    if _client is None:
        _client = TeamsFlightClient()
    return _client


class TeamsFlightClient:

    def __init__(self, host=None, port=None):
        host = host or resolve_internal_service_host()
        port = port or int(required_config("teams_server", "server.port"))

        tls_root = load_tls_root()
        if tls_root:
            self._client = connect_flight(
                f"grpc+tls://{host}:{port}",
                tls_root_certs=tls_root,
                disable_server_verification=True,
            )
            logging.info("Teams Flight client TLS -> %s:%s", host, port)
        else:
            self._client = connect_flight(f"grpc://{host}:{port}")
            logging.info("Teams Flight client -> %s:%s", host, port)

    @staticmethod
    def _load_tls_root():
        return load_tls_root()

    def _call(self, action_type, body=None, timeout=300):
        from flight_server import run_context
        body = run_context.inject_into_body(dict(body or {}))
        raw_body = json.dumps(body, default=str).encode('utf-8')
        action = flight.Action(action_type, raw_body)
        options = flight.FlightCallOptions(timeout=timeout)
        results = list(self._client.do_action(action, options=options))
        if not results:
            return None
        data = json.loads(results[0].body.to_pybytes())
        if isinstance(data, dict) and data.get('success') is False:
            raise RuntimeError(data.get('error', 'Teams server error'))
        return data

    # ── Async query (ai_chat pattern) ────────────────────────────

    def submit_query(self, question, session_id='default', user_id='anonymous', source_system='ai_chat',
                     genie_type=None, model=None):
        r = self._call('submit_query', {
            'question': question, 'session_id': session_id,
            'user_id': user_id, 'source_system': source_system,
            'genie_type': genie_type, 'model': model,
        })
        return r.get('request_id')

    def get_request_status(self, request_id):
        return self._call('get_request_status', {'request_id': request_id})

    def cancel_request(self, request_id):
        r = self._call('cancel_request', {'request_id': request_id})
        return r.get('cancelled', False) if r else False

    # ── Sync query (teams bot / direct API) ──────────────────────

    def query_sync(self, question, session_id='default', user_id=None, source_system='direct_api',
                   mock=False, route=None, model_deployment_name=None):
        body = {
            'question': question, 'session_id': session_id,
            'user_id': user_id or session_id, 'source_system': source_system, 'mock': mock,
        }
        if route:
            body['route'] = route
        if model_deployment_name:
            body['model_deployment_name'] = model_deployment_name
        return self._call('query_sync', body)

    def ai_query(self, question, session_id='default', user_id=None, source_system='teams_bot'):
        return self._call('ai_query', {
            'question': question, 'session_id': session_id,
            'user_id': user_id or session_id, 'source_system': source_system,
        })

    def ai_query_streaming(self, question, session_id='default', user_id=None, source_system='teams_bot'):
        import pandas as pd
        ticket_data = json.dumps({
            'type': 'ai_stream',
            'question': question,
            'session_id': session_id,
            'user_id': user_id or session_id,
            'source_system': source_system,
        }).encode('utf-8')
        
        ticket = flight.Ticket(ticket_data)
        reader = self._client.do_get(ticket)
        
        for chunk in reader:
            batch = chunk.data
            if batch is None or batch.num_rows == 0:
                continue
            json_str = batch.to_pydict()['data'][0]
            record = json.loads(json_str)
            df = pd.DataFrame(record['data'], columns=record['columns']) if record.get('data') else pd.DataFrame(columns=record.get('columns', []))
            if record.get('attrs'):
                df.attrs = record['attrs']
            yield df

    # ── LLM / tables (for bot proxy) ───────────────────────────

    def call_llm(self, messages, timer_label=''):
        r = self._call('call_llm', {'messages': messages, 'timer_label': timer_label})
        return r.get('response', '') if r else ''

    def get_available_tables(self):
        r = self._call('get_available_tables')
        return r.get('tables', '') if r else ''

    def clear_conversation(self, session_id='default', user_id=None, genie_type=None, model=None):
        return self._call('clear_conversation', {
            'session_id': session_id,
            'user_id': user_id or session_id,
            'genie_type': genie_type,
            'model': model,
        })

    def steer_session(self, session_id, guidance, user_name=None):
        r = self._call('steer_session', {
            'session_id': session_id,
            'guidance': guidance,
            'user_name': user_name or '',
        })
        return bool(r and r.get('steered'))

    # ── Health ───────────────────────────────────────────────────

    def health_check(self):
        return self._call('health_check', timeout=10)

    def close(self):
        self._client.close()


class GenieFlightProxy:
    """Drop-in replacement for genie_client that delegates to TeamsFlightServer over gRPC.
    Used by TeamsGenieBot so all LLM/SQL work runs on the Flight server, not the web process.

    SQL queries go to the configured teams server.
    /ai queries go to the configured main Flight server for full BYOT + sibling tools.
    """

    def __init__(self):
        self._fc = get_client()
        self._flight = None
        self._flight_url = None

    def _reset_flight_client(self):
        if self._flight is not None:
            try:
                self._flight.close()
            except Exception:
                pass
        self._flight = None
        self._flight_url = None

    def _get_flight_client(self):
        if self._flight is None:
            from flight_server.client_sdk.flight_transport import connect_flight_server
            self._flight, self._flight_url = connect_flight_server()
            logging.info("GenieFlightProxy: connected to main Flight server at %s", self._flight_url)
        return self._flight

    def _flight_action_chunk(self, action_type, body):
        fc = self._get_flight_client()
        from flight_server import run_context
        payload = json.dumps(run_context.inject_into_body(dict(body or {}))).encode('utf-8')
        results = fc.do_action(flight.Action(action_type, payload))
        for result in results:
            return result.body.to_pybytes()
        return b''

    def _flight_action_progress(self, session_id):
        chunk = self._flight_action_chunk('get_progress', {'session_id': session_id})
        if not chunk:
            return None
        try:
            text = chunk.decode('utf-8')
            if text.startswith('{'):
                msg = json.loads(text)
                if msg.get('error'):
                    return None
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        parsed = self._parse_stream_chunk(chunk)
        if parsed is None or not hasattr(parsed, 'columns') or len(parsed) == 0:
            return None
        return parsed.iloc[0].to_dict()

    def _flight_action_result(self, session_id):
        chunk = self._flight_action_chunk('get_result', {'session_id': session_id})
        if not chunk:
            return None
        try:
            text = chunk.decode('utf-8')
            if text.startswith('{'):
                msg = json.loads(text)
                if msg.get('error'):
                    logging.info("get_result: %s", msg['error'])
                    return None
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        parsed = self._parse_stream_chunk(chunk)
        if parsed is None or isinstance(parsed, dict):
            return None
        if self._is_progress_frame(parsed):
            return None
        return parsed

    def query_with_sql(self, question, session_id='default', source_system='teams_bot',
                       enable_logging=True, model_deployment_name=None):
        return self._fc.query_sync(
            question, session_id=session_id, user_id=session_id, source_system=source_system,
            route='riskintern', model_deployment_name=model_deployment_name,
        )

    def ai_query(self, question, session_id='default', source_system='teams_bot'):
        return self._fc.ai_query(
            question, session_id=session_id, user_id=session_id, source_system=source_system,
        )

    def ai_query_streaming(self, question, session_id='default', user_id=None, source_system='teams_bot', files=None):
        """Route /ai queries to main Flight server for full BYOT + sibling tools.

        Sends cli_agent action via gRPC to the configured Flight server.
        Streams progress as DataFrames, yields final result as DataFrame.
        If the gRPC stream dies mid-flight, polls get_progress/get_result until
        the server finishes (same recovery path as AgentClient).
        """
        import time

        raw_user = (user_id or session_id or 'unknown').split('_')[0]
        logging.info(
            "[AI_TRACE] teams_proxy start route=general_ai flight_action=cli_agent session=%s user=%s prompt_len=%d files=%d",
            session_id,
            raw_user,
            len(question or ""),
            len(files or []),
        )

        fc = self._get_flight_client()
        from flight_server import run_context
        payload = {
            'prompt': question,
            'session_id': session_id,
            'user_name': raw_user,
            'client_type': 'teams',
            'request_id': str(uuid.uuid4()),
            'route': 'general_ai',
            'capabilities': [
                'teams_attachment_ingest',
                'teams_file_consent_delivery',
                'teams_cards',
                'no_local_filesystem',
            ],
            'system_prompt': (
                "You are an AI assistant responding via Microsoft Teams. "
                "Write the final response for business users in clean Teams Markdown, not as an execution log. "
                "Lead with the answer, then use short sections such as Key drivers, Results, Notes, or Next steps only when useful. "
                "Keep prose tight and scannable; avoid dumping reasoning, plans, tool calls, SQL mechanics, or orchestration details into the final answer. "
                "For data-heavy results, publish the table/data artifact and summarize the important rows instead of pasting large tables into prose. "
                "Use confidence only when it is grounded in query/data quality, not as decorative filler. "
                "Teams has no local C: drive path; generated files must be delivered as Teams attachments, cards, or links. "
                "If a Teams notifier is connected, proactively message users via "
                "teams_bot_send_message(user, text), teams_bot_send_html(user, html), or "
                "teams_bot_send_card(user, title, ...). If those tools are not in your list, "
                "say you cannot notify proactively rather than improvising with credentials. "
                "Use search_tools() to discover additional sibling client tools "
                "(Excel, Outlook, CLI on the user's machine)."
            ),
        }
        if files:
            payload['files'] = files
        body = json.dumps(run_context.inject_into_body(payload)).encode('utf-8')

        action = flight.Action("cli_agent", body)
        got_final_result = False
        received_any = False
        connect_attempts = 0

        while True:
            connect_attempts += 1
            try:
                logging.info(
                    "[AI_TRACE] teams_proxy flight_start route=general_ai flight_action=cli_agent session=%s attempt=%d",
                    session_id,
                    connect_attempts,
                )
                results = fc.do_action(action)
                for result in results:
                    received_any = True
                    chunk = result.body.to_pybytes()
                    parsed = self._parse_stream_chunk(chunk)
                    if parsed is not None:
                        is_progress = self._is_progress_frame(parsed)
                        if not isinstance(parsed, dict) and not is_progress:
                            got_final_result = True
                            logging.info(
                                "[AI_TRACE] teams_proxy final session=%s rows=%d cols=%s attrs(reasoning=%d sql=%d code=%d vba=%d data_artifacts=%d file_artifacts=%d)",
                                session_id,
                                len(parsed),
                                list(parsed.columns),
                                len(parsed.attrs.get('reasoning', '') or ''),
                                len(parsed.attrs.get('sql', '') or ''),
                                len(parsed.attrs.get('code', '') or ''),
                                len(parsed.attrs.get('vba_code', '') or ''),
                                len(parsed.attrs.get('data_artifacts', '') or ''),
                                len(parsed.attrs.get('file_artifacts', '') or ''),
                            )
                        yield parsed
                return
            except Exception as stream_err:
                logging.warning(
                    "[AI_TRACE] teams_proxy flight_error route=general_ai flight_action=cli_agent session=%s attempt=%d received_any=%s error=%s",
                    session_id,
                    connect_attempts,
                    received_any,
                    stream_err,
                )
                if got_final_result:
                    return
                if not received_any and is_recoverable_stream_error(stream_err) and connect_attempts <= 3:
                    backoff = connect_attempts * 3
                    logging.warning(
                        "Flight server unreachable (attempt %d), reconnecting in %ds: %s",
                        connect_attempts, backoff, stream_err,
                    )
                    self._reset_flight_client()
                    time.sleep(backoff)
                    fc = self._get_flight_client()
                    continue
                logging.warning("gRPC stream failed, attempting result recovery: %s", stream_err)
                try:
                    for kind, payload in self._iter_recovery(session_id, stream_err):
                        if kind == "progress":
                            frame = self._recovery_progress_frame(payload)
                            if frame is not None:
                                yield frame
                        else:
                            logging.info(
                                "[AI_TRACE] teams_proxy recovered session=%s rows=%d",
                                session_id, len(payload),
                            )
                            yield payload
                            return
                except RuntimeError:
                    pass
                if is_recoverable_stream_error(stream_err):
                    raise RuntimeError(
                        "Connection to the AI backend was lost while your request was still running. "
                        "Please try again — the server may still be finishing the previous attempt."
                    ) from stream_err
                raise

    def steer_session(self, session_id, guidance, user_name=None):
        chunk = self._flight_action_chunk('steer_session', {
            'session_id': session_id,
            'guidance': guidance,
            'user_name': user_name or '',
        })
        if not chunk:
            return False
        parsed = self._parse_stream_chunk(chunk)
        if parsed is not None and hasattr(parsed, 'columns') and 'steered' in parsed.columns and len(parsed):
            return bool(parsed['steered'].iloc[0])
        data = json.loads(chunk.decode('utf-8'))
        return bool(data.get('steered'))

    @staticmethod
    def _is_progress_frame(df) -> bool:
        return (
            hasattr(df, 'columns') and 'type' in df.columns and len(df) > 0
            and str(df['type'].iloc[0]) == 'progress'
        )

    @staticmethod
    def _recovery_progress_frame(snapshot):
        import pandas as pd
        if not snapshot:
            return None
        step = str(snapshot.get('step') or '')
        if step in ('heartbeat', 'llm_call', 'usage_update'):
            return None
        summary = str(snapshot.get('summary') or '')
        if step == 'working' and not summary:
            return pd.DataFrame([{
                'type': 'progress',
                'step': 'recovery',
                'summary': 'Connection lost — waiting for server to finish...',
                'iteration': 0,
                'max_iterations': 0,
            }])
        return pd.DataFrame([{**snapshot, 'type': 'progress'}])

    def _iter_recovery(self, session_id, stream_err):
        def on_progress(snapshot):
            logging.info(
                "[AI_TRACE] teams_proxy recovery_progress session=%s step=%s",
                session_id, snapshot.get('step'),
            )

        return iter_session_recovery(
            session_id,
            reconnect=self._reset_flight_client,
            get_progress=self._flight_action_progress,
            get_result=self._flight_action_result,
            on_progress_log=on_progress,
        )

    def _parse_stream_chunk(self, chunk: bytes):
        """Parse a single gRPC stream chunk. Returns DataFrame, progress dict, or None."""
        import pandas as pd
        import pyarrow.ipc as ipc
        import io

        try:
            text = chunk.decode('utf-8')
            if text.startswith('{'):
                msg = json.loads(text)
                if msg.get('error'):
                    logging.error(f"Flight server error: {msg['error']}")
                    df = pd.DataFrame([{'summary': msg['error']}])
                    df.attrs['reasoning'] = msg['error']
                    return df
                if msg.get('type') == 'progress' or msg.get('step'):
                    return pd.DataFrame([{**msg, 'type': 'progress'}])
                return None
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass

        try:
            reader = ipc.open_stream(io.BytesIO(chunk))
            table = reader.read_all()
            df = table.to_pandas()
            metadata = table.schema.metadata or {}
            for key, attr in (
                (b'reasoning', 'reasoning'),
                (b'sql', 'sql'),
                (b'code', 'code'),
                (b'vba_code', 'vba_code'),
                (b'data_artifacts', 'data_artifacts'),
                (b'file_artifacts', 'file_artifacts'),
            ):
                raw = metadata.get(key)
                if raw:
                    val = raw.decode('utf-8', errors='replace').strip()
                    if val:
                        df.attrs[attr] = val
            if 'summary' in df.columns and len(df) > 0 and not df.attrs.get('reasoning'):
                df.attrs['reasoning'] = str(df['summary'].iloc[0])
            for col, attr in [('sql', 'sql'), ('code', 'code'), ('vba_code', 'vba_code'),
                              ('data_artifacts', 'data_artifacts'), ('file_artifacts', 'file_artifacts')]:
                if col in df.columns and len(df) > 0 and not df.attrs.get(attr):
                    val = str(df[col].iloc[0]).strip()
                    if val:
                        df.attrs[attr] = val
            logging.info(
                "[AI_TRACE] teams_proxy parsed_result rows=%d cols=%s metadata_keys=%s attrs(reasoning=%d sql=%d code=%d vba=%d data_artifacts=%d file_artifacts=%d)",
                len(df),
                list(df.columns),
                [k.decode('utf-8', errors='replace') for k in metadata.keys()],
                len(df.attrs.get('reasoning', '') or ''),
                len(df.attrs.get('sql', '') or ''),
                len(df.attrs.get('code', '') or ''),
                len(df.attrs.get('vba_code', '') or ''),
                len(df.attrs.get('data_artifacts', '') or ''),
                len(df.attrs.get('file_artifacts', '') or ''),
            )
            return df
        except Exception as e:
            logging.debug(f"Flight result parse: {e}")
            return None

    def _call_llm(self, messages, timer_label=''):
        return self._fc.call_llm(messages, timer_label=timer_label)

    def health_check(self):
        return self._fc.health_check()

    def get_available_tables(self):
        return self._fc.get_available_tables()

    def clear_conversation(self, session_id):
        return self._fc.clear_conversation(session_id)
