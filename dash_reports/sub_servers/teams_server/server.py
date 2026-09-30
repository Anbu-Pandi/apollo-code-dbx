import json
import logging
import os
import queue
import threading
import uuid
from datetime import datetime
from enum import Enum

import pyarrow.flight as flight

from apo_config.config import required_config

APP = 'teams_server'


class RequestStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"


class _ChatRequest:
    __slots__ = ('request_id', 'question', 'session_id', 'user_id',
                 'source_system', 'genie_type', 'model',
                 'status', 'response', 'sql', 'error',
                 'created_at', 'started_at', 'completed_at', 'progress')

    def __init__(self, request_id, question, session_id='default',
                 user_id='anonymous', source_system='ai_chat',
                 genie_type=None, model=None):
        self.request_id = request_id
        self.question = question
        self.session_id = session_id
        self.user_id = user_id
        self.source_system = source_system
        self.genie_type = genie_type
        self.model = model
        self.status = RequestStatus.PENDING
        self.response = None
        self.sql = None
        self.error = None
        self.created_at = datetime.now()
        self.started_at = None
        self.completed_at = None
        self.progress = 0

    def to_dict(self):
        duration = None
        if self.started_at:
            end = self.completed_at or datetime.now()
            duration = (end - self.started_at).total_seconds()
        return {
            'request_id': self.request_id,
            'question': self.question,
            'session_id': self.session_id,
            'user_id': self.user_id,
            'source_system': self.source_system,
            'genie_type': self.genie_type,
            'model': self.model,
            'status': self.status.value,
            'response': self.response,
            'sql': self.sql,
            'error': self.error,
            'progress': self.progress,
            'duration': duration,
        }


class TeamsFlightServer(flight.FlightServerBase):
    _request_counter = 0
    _counter_lock = threading.Lock()

    def __init__(self, host=None, port=None, **kwargs):
        host = host or required_config("teams_server", "server.host")
        port = port or int(required_config("teams_server", "server.port"))
        ssl_ctx = self._get_ssl_context()
        if ssl_ctx:
            with open(ssl_ctx[0], 'rb') as f:
                tls_cert = f.read()
            with open(ssl_ctx[1], 'rb') as f:
                tls_key = f.read()
            location = flight.Location.for_grpc_tls(host, port)
            super().__init__(location, tls_certificates=[(tls_cert, tls_key)], **kwargs)
            logging.info("Teams Flight TLS on %s:%s", host, port)
        else:
            location = flight.Location.for_grpc_tcp(host, port)
            super().__init__(location, **kwargs)
            logging.info("Teams Flight (no TLS) on %s:%s", host, port)

        self._host, self._port = host, port
        self._genie_clients: dict[tuple, object] = {}
        self._genie_lock = threading.Lock()
        self._default_genie_type = 'custom'
        self._default_model = None
        self._requests: dict[str, _ChatRequest] = {}
        self._request_queue = queue.Queue()
        self._req_lock = threading.Lock()
        self._worker_running = False
        self._worker_thread = None
        self._start_worker()

    @staticmethod
    def _get_ssl_context():
        try:
            from apo_config.config import config
            cert_folder = config.getconfig(APP, 'certificate_dir')
            if not cert_folder:
                cert_folder = config.getconfig('dash_reports', 'certificate_dir')
            if cert_folder:
                cp = os.path.join(cert_folder, 'server.crt')
                kp = os.path.join(cert_folder, 'server.key')
                if os.path.exists(cp) and os.path.exists(kp):
                    return cp, kp
        except Exception:
            pass
        return None

    # ── Worker ───────────────────────────────────────────────────

    def _start_worker(self):
        if not self._worker_running:
            self._worker_running = True
            self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name='GenieWorker')
            self._worker_thread.start()
            logging.info("Genie worker started")

    def _worker_loop(self):
        while self._worker_running:
            try:
                request_id = self._request_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            self._process_request(request_id)
            if len(self._requests) > 200:
                self._cleanup_old()

    def _process_request(self, request_id):
        with self._req_lock:
            req = self._requests.get(request_id)
            if not req or req.status != RequestStatus.PENDING:
                return
            req.status = RequestStatus.PROCESSING
            req.started_at = datetime.now()
            req.progress = 10

        logging.info("Processing %s [user=%s src=%s]: %s",
                 request_id[:8], req.user_id, req.source_system, req.question[:80])
        try:
            client = self._genie_for(req.genie_type, req.model)
            with self._req_lock:
                if req.status == RequestStatus.CANCELLED:
                    return
                req.progress = 30

            result = client.query_with_sql(
                req.question,
                session_id=req.session_id,
                source_system=req.source_system,
                enable_logging=True,
            )
            with self._req_lock:
                if req.status == RequestStatus.CANCELLED:
                    return
                req.response = result.get('response', 'No response available')
                req.sql = result.get('sql')
                req.status = RequestStatus.COMPLETED
                req.completed_at = datetime.now()
                req.progress = 100
            logging.info("Completed %s [user=%s] in %.1fs", request_id[:8], req.user_id, req.to_dict()['duration'] or 0)
        except Exception as e:
            logging.exception("Failed %s", request_id[:8])
            with self._req_lock:
                req.status = RequestStatus.ERROR
                req.error = str(e)
                req.completed_at = datetime.now()
                req.progress = 0

    def _cleanup_old(self, max_age_hours=24):
        cutoff = datetime.now().timestamp() - (max_age_hours * 3600)
        with self._req_lock:
            stale = [rid for rid, r in self._requests.items()
                     if r.status in (RequestStatus.COMPLETED, RequestStatus.ERROR, RequestStatus.CANCELLED)
                     and r.created_at.timestamp() < cutoff]
            for rid in stale:
                del self._requests[rid]
        if stale:
            logging.info("Cleaned up %d old requests", len(stale))

    # ── Genie client management ──────────────────────────────────

    def _genie_for(self, genie_type=None, model=None):
        key = (genie_type or self._default_genie_type, model or self._default_model)
        with self._genie_lock:
            client = self._genie_clients.get(key)
            if client is None:
                client = self._create_genie(*key)
                self._genie_clients[key] = client
            return client

    def _create_genie(self, genie_type, model):
        os.environ.update({
            'USE_DATABRICKS_GENIE': str(genie_type == 'databricks'),
            'USE_CUSTOM_GENIE': str(genie_type == 'custom'),
            'USE_CUSTOM_AGENT': str(genie_type == 'agent'),
            'USE_GLEAN_AGENT': str(genie_type == 'glean'),
            'USE_MCP_TOOLBOX': str(genie_type == 'mcp'),
        })
        if model:
            os.environ['MODEL_DEPLOYMENT_NAME'] = model
        else:
            os.environ.pop('MODEL_DEPLOYMENT_NAME', None)

        from genai.sql_genie.clients.factory import get_genie_client
        client = get_genie_client()
        logging.info("Genie client created: type=%s model=%s", genie_type, model)
        return client

    # ── JSON helpers ─────────────────────────────────────────────

    @staticmethod
    def _jr(obj):
        return flight.Result(json.dumps(obj, default=str).encode('utf-8'))

    @staticmethod
    def _er(msg):
        return flight.Result(json.dumps({'success': False, 'error': str(msg)}).encode('utf-8'))

    # ── Flight protocol ──────────────────────────────────────────

    def list_actions(self, context):
        for name in [
            'health_check',
            'submit_query', 'get_request_status', 'cancel_request',
            'query_sync', 'ai_query', 'call_llm', 'get_available_tables',
            'clear_conversation', 'steer_session',
        ]:
            yield (name, name)

    def do_get(self, context, ticket):
        import pyarrow as pa
        
        try:
            ticket_data = json.loads(ticket.ticket.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise flight.FlightServerError("Invalid ticket format")
        
        query_type = ticket_data.get('type', '')
        
        if query_type == 'ai_stream':
            return self._stream_ai_query(ticket_data)
        else:
            raise flight.FlightServerError(f"Unknown ticket type: {query_type}")

    def _stream_ai_query(self, ticket_data):
        import pyarrow as pa
        import pandas as pd
        from flight_server.action_handlers.ai_actions import handle_ai_streaming
        
        question = ticket_data.get('question', '')
        session_id = ticket_data.get('session_id', 'default')
        user_id = ticket_data.get('user_id', session_id)
        source_system = ticket_data.get('source_system', 'teams_bot')
        
        logging.info("AI stream [user=%s src=%s]: %s", user_id, source_system, question[:80])
        
        body = {
            'question': question,
            'session_id': session_id,
            'user_name': user_id,
            'source': source_system
        }
        
        schema = pa.schema([('data', pa.string())])
        
        def generate_batches():
            for item in handle_ai_streaming(body):
                if item is None:
                    continue
                # Convert dicts to single-row DataFrames for backward compatibility
                if isinstance(item, dict):
                    item.setdefault("type", "progress")
                    item = pd.DataFrame([item])
                record = {
                    'columns': list(item.columns),
                    'data': item.to_dict('records'),
                    'attrs': item.attrs if hasattr(item, 'attrs') else {}
                }
                json_str = json.dumps(record, default=str)
                batch = pa.RecordBatch.from_pydict({'data': [json_str]}, schema=schema)
                yield batch
            logging.info("AI stream completed [user=%s]", user_id)
        
        return flight.GeneratorStream(schema, generate_batches())

    def do_action(self, context, action):
        t = threading.current_thread()
        if t.name.startswith('Dummy'):
            with self._counter_lock:
                self._request_counter += 1
            t.name = f"Teams_{self._request_counter}"

        body = {}
        if action.body and action.body.to_pybytes():
            try:
                body = json.loads(action.body.to_pybytes().decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

        from flight_server import run_context
        run_context.adopt_body(body, action_type=action.type)

        handler = getattr(self, f'_act_{action.type}', None)
        if handler is None:
            yield self._er(f'Unknown action: {action.type}')
            return
        try:
            result = handler(body)
            if hasattr(result, '__next__'):
                yield from result
            else:
                yield result
        except Exception as e:
            logging.exception("Action %s failed", action.type)
            yield self._er(str(e))

    # ── Actions ──────────────────────────────────────────────────

    def _act_health_check(self, b):
        active = sum(1 for r in self._requests.values() if r.status == RequestStatus.PROCESSING)
        return self._jr({
            'success': True,
            'status': 'healthy' if self._worker_running else 'unhealthy',
            'client_type': self._default_genie_type,
            'service': APP,
            'worker_running': self._worker_running,
            'genie_ready': bool(self._genie_clients),
            'genie_type': self._default_genie_type,
            'model': self._default_model,
            'active_requests': active,
            'total_requests': len(self._requests),
        })

    def _act_submit_query(self, b):
        question = b.get('question', '')
        if not question:
            return self._er('Missing question')
        session_id = b.get('session_id', 'default')
        user_id = b.get('user_id', 'anonymous')
        source_system = b.get('source_system', 'ai_chat')
        genie_type = b.get('genie_type')
        model = b.get('model')
        request_id = str(uuid.uuid4())
        with self._req_lock:
            self._requests[request_id] = _ChatRequest(
                request_id, question, session_id,
                user_id=user_id, source_system=source_system,
                genie_type=genie_type, model=model,
            )
            self._request_queue.put(request_id)
        logging.info("Queued %s [user=%s src=%s type=%s model=%s]: %s",
                     request_id[:8], user_id, source_system, genie_type, model, question[:80])
        return self._jr({'request_id': request_id, 'status': 'pending'})

    def _act_get_request_status(self, b):
        request_id = b.get('request_id', '')
        with self._req_lock:
            req = self._requests.get(request_id)
        if not req:
            return self._er(f'Request not found: {request_id[:8]}')
        return self._jr(req.to_dict())

    def _act_cancel_request(self, b):
        request_id = b.get('request_id', '')
        with self._req_lock:
            req = self._requests.get(request_id)
            if req and req.status == RequestStatus.PENDING:
                req.status = RequestStatus.CANCELLED
                req.completed_at = datetime.now()
                logging.info("Cancelled %s [user=%s]", request_id[:8], req.user_id)
                return self._jr({'cancelled': True})
        return self._jr({'cancelled': False})

    def _act_query_sync(self, b):
        question = b.get('question', '')
        if not question:
            return self._er('Missing question')
        session_id = b.get('session_id', 'default')
        user_id = b.get('user_id', session_id)
        source_system = b.get('source_system', 'direct_api')
        mock = b.get('mock', False)
        model_deployment_name = b.get('model_deployment_name')
        logging.info("Sync query [user=%s src=%s]: %s", user_id, source_system, question[:80])

        if mock:
            import pandas as pd
            import numpy as np
            df = pd.DataFrame(np.random.randn(5, 5),
                              columns=['Column_A', 'Column_B', 'Column_C', 'Column_D', 'Column_E']).round(2)
            table_md = df.to_markdown(index=False)
            return self._jr({
                'status': 'success',
                'response': f'**Mock Response for: "{question}"**\n\n**DATA RESULTS**\n\n{table_md}\n\n**Confidence:** 100% (Mock Data)',
                'sql': 'SELECT * FROM mock_table LIMIT 5',
                'question': question,
                'mock': True,
                'data': df.to_dict('records'),
            })

        client = self._genie_for(b.get('genie_type'), b.get('model'))
        result = client.query_with_sql(question, session_id=session_id,
                                       source_system=source_system, enable_logging=True,
                                       model_deployment_name=model_deployment_name)
        logging.info("Sync query completed [user=%s]", user_id)
        return self._jr({
            'status': 'success',
            'response': result.get('response', 'No response available'),
            'sql': result.get('sql'),
            'log_id': result.get('log_id'),
            'question': question,
            'mock': False,
        })

    def _act_steer_session(self, b):
        from flight_server import active_sessions
        steered = active_sessions.steer(
            b.get('session_id', ''),
            b.get('guidance', ''),
            user_name=b.get('user_name', ''),
        )
        return self._jr({'steered': steered, 'session_id': b.get('session_id', '')})

    def _act_ai_query(self, b):
        from flight_server.action_handlers.ai_actions import handle_ai_streaming
        import pandas as pd
        
        question = b.get('question', '')
        if not question:
            return self._er('Missing question')
        session_id = b.get('session_id', 'default')
        user_id = b.get('user_id', session_id)
        source_system = b.get('source_system', 'teams_bot')
        logging.info("AI query [user=%s src=%s]: %s", user_id, source_system, question[:80])
        
        body = {
            'question': question,
            'session_id': session_id,
            'user_name': user_id,
            'source': source_system
        }
        
        final_df = None
        checkpoints = []
        for item in handle_ai_streaming(body):
            if item is None:
                continue
            if isinstance(item, dict):
                # Progress dict
                step = item.get('step', '')
                if step == 'checkpoint':
                    summary = item.get('summary', '')
                    if summary:
                        checkpoints.append(summary)
            elif not item.empty:
                final_df = item
        
        logging.info("AI query completed [user=%s]", user_id)
        
        if final_df is None:
            return self._jr({'status': 'success', 'response': 'No result', 'dataframe': None})
        
        def _pick(name):
            if hasattr(final_df, 'attrs'):
                val = final_df.attrs.get(name)
                if val:
                    return val
            if name in final_df.columns and len(final_df) > 0:
                val = str(final_df[name].iloc[0]).strip()
                if val:
                    return val
            return None

        reasoning = _pick('reasoning') or ''
        sql = _pick('sql')
        code = _pick('code')
        vba_code = _pick('vba_code')
        
        checkpoint_text = ""
        if checkpoints:
            done = [f"✅ {c}" for c in checkpoints]
            checkpoint_text = "\n".join(done)

        return self._jr({
            'status': 'success',
            'response': (checkpoint_text + "\n\n" + reasoning) if checkpoint_text else reasoning,
            'sql': sql,
            'code': code,
            'vba_code': vba_code,
            'question': question,
            'dataframe': final_df.to_dict('records') if not final_df.empty else None,
            'columns': list(final_df.columns) if not final_df.empty else [],
        })

    def _act_clear_conversation(self, b):
        session_id = b.get('session_id', 'default')
        user_id = b.get('user_id', session_id)
        client = self._genie_for(b.get('genie_type'), b.get('model'))
        if hasattr(client, 'clear_conversation'):
            client.clear_conversation(session_id)
            logging.info("Cleared conversation [user=%s session=%s]", user_id, session_id)
        return self._jr({'success': True, 'session_id': session_id})

    def _act_call_llm(self, b):
        messages = b.get('messages', [])
        timer_label = b.get('timer_label', '')
        if not messages:
            return self._er('Missing messages')
        logging.info("LLM call [label=%s]: %d message(s)", timer_label, len(messages))
        client = self._genie_for()
        result = client._call_llm(messages, timer_label=timer_label)
        logging.info("LLM call completed [label=%s]", timer_label)
        return self._jr({'response': result})

    def _act_get_available_tables(self, b):
        client = self._genie_for()
        tables = client.get_available_tables()
        return self._jr({'tables': tables})
