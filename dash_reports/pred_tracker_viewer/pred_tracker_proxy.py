import logging
import requests
from flask import Response, request, stream_with_context

from common.service_discovery import resolve_internal_service_url

try:
    from flask_sock import Sock
    import websocket
    HAS_WEBSOCKET = True
except ImportError:
    HAS_WEBSOCKET = False

def _pred_tracker_url():
    return resolve_internal_service_url("pred_tracker")


def _pred_tracker_ws_url():
    return resolve_internal_service_url("pred_tracker", scheme_key="server.ws_scheme", path="ws")

def setup_pred_tracker_proxy(server):
    """Setup a proxy route to forward requests to pred_tracker service.
    Returns the Sock instance if created, so it can be shared."""
    sock = None

    if HAS_WEBSOCKET:
        sock = Sock(server)
        
        @sock.route('/pred-tracker-api/ws')
        def pred_tracker_websocket(ws):
            """Proxy WebSocket connections to pred_tracker."""
            import ssl
            backend_ws = None
            try:
                # Connect to backend WSS with self-signed cert (skip verification)
                # Match exact params that work in command-line test
                backend_ws = websocket.create_connection(
                    _pred_tracker_ws_url(),
                    sslopt={"cert_reqs": ssl.CERT_NONE, "check_hostname": False},
                    timeout=10
                )
                logging.info("WebSocket proxy connected to pred_tracker")
                
                import threading
                import queue
                
                stop_event = threading.Event()
                msg_count = [0]
                
                def forward_from_backend():
                    """Forward messages from pred_tracker to browser."""
                    while not stop_event.is_set():
                        try:
                            backend_ws.settimeout(1.0)
                            message = backend_ws.recv()
                            if message:
                                ws.send(message)
                                msg_count[0] += 1
                                if msg_count[0] <= 3 or msg_count[0] % 50 == 0:
                                    logging.info(f"WebSocket proxy: forwarded {msg_count[0]} msgs to browser")
                        except websocket.WebSocketTimeoutException:
                            continue  # Timeout is ok, just check stop_event
                        except Exception as e:
                            logging.error(f"WebSocket proxy backend error: {e}")
                            stop_event.set()
                            break
                    logging.info(f"WebSocket proxy: backend stopped ({msg_count[0]} msgs total)")
                
                # Start backend forwarder thread
                backend_thread = threading.Thread(target=forward_from_backend, daemon=True)
                backend_thread.start()
                
                # Main thread: wait for client disconnect (browser closing connection)
                try:
                    while not stop_event.is_set():
                        try:
                            data = ws.receive()  # Blocks until client sends or disconnects
                            if data:
                                backend_ws.send(data)
                        except Exception:
                            break  # Client disconnected
                except Exception as e:
                    logging.info(f"WebSocket proxy: client disconnected ({e})")
                
                stop_event.set()
                backend_thread.join(timeout=2)
                logging.info(f"WebSocket proxy: connection closed ({msg_count[0]} msgs forwarded)")
                
            except Exception as e:
                logging.error(f"WebSocket proxy error: {e}")
            finally:
                if backend_ws:
                    backend_ws.close()
    
    @server.route('/pred-tracker-api/', defaults={'path': ''})
    @server.route('/pred-tracker-api/<path:path>')
    def pred_tracker_proxy(path):
        """Proxy HTTP requests to the configured pred_tracker service."""
        
        # Reject WebSocket requests if WebSocket proxy not available
        if path == 'ws' and not HAS_WEBSOCKET:
            return Response("WebSocket proxy not available - install flask-sock", status=501)
        
        try:
            backend_url = _pred_tracker_url()
            url = f"{backend_url}/{path}"
            if request.query_string:
                url = f"{url}?{request.query_string.decode('utf-8')}"
            
            # Prepare headers (exclude host/connection)
            headers = {key: value for key, value in request.headers if key.lower() not in ['host', 'connection']}
            
            # Forward the request (verify=False to skip SSL verification for self-signed cert)
            if request.method == 'GET':
                resp = requests.get(url, headers=headers, stream=True, timeout=30, verify=False)
            elif request.method == 'POST':
                resp = requests.post(url, headers=headers, data=request.get_data(), stream=True, timeout=30, verify=False)
            else:
                resp = requests.request(request.method, url, headers=headers, data=request.get_data(), stream=True, timeout=30, verify=False)
            
            # Modify HTML to fix paths for iframe context
            if path == '' or path == 'index.html':
                content = resp.content.decode('utf-8', errors='ignore')
                
                # Fix WebSocket path
                content = content.replace(
                    'ws://${window.location.host}/ws',
                    'wss://${window.location.host}/pred-tracker-api/ws' if request.is_secure else 'ws://${window.location.host}/pred-tracker-api/ws'
                )
                
                # Fix API paths - make them relative to /pred-tracker-api/
                content = content.replace("fetch('/api/", "fetch('/pred-tracker-api/api/")
                content = content.replace('fetch("/api/', 'fetch("/pred-tracker-api/api/')
                
                return Response(content, status=resp.status_code, content_type='text/html')
            
            # Create response with proper headers for other content
            excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
            response_headers = [(name, value) for name, value in resp.raw.headers.items() 
                              if name.lower() not in excluded_headers]
            
            def generate():
                for chunk in resp.iter_content(chunk_size=8192):
                    yield chunk
            
            return Response(stream_with_context(generate()), 
                          status=resp.status_code, 
                          headers=response_headers,
                          direct_passthrough=True)
            
        except requests.exceptions.SSLError as ssl_error:
            logging.error(f"SSL error connecting to pred_tracker: {ssl_error}")
            return Response("""
                <html>
                <body style="font-family: Arial; padding: 50px; text-align: center;">
                    <h1>🔒 SSL Certificate Issue</h1>
                    <p>Cannot verify the pred_tracker SSL certificate.</p>
                    <p>This is normal for self-signed certificates.</p>
                </body>
                </html>
            """, status=503)
        except requests.exceptions.ConnectionError:
            backend_url = _pred_tracker_url()
            logging.error("Cannot connect to pred_tracker at %s", backend_url)
            return Response(f"""
                <html>
                <body style="font-family: Arial; padding: 50px; text-align: center;">
                    <h1>🎯 Pred Tracker Service Not Running</h1>
                    <p>The pred_tracker service is not accessible at {backend_url}</p>
                    <p>Please start it with: <code>pred-tracker --port 8010</code></p>
                </body>
                </html>
            """, status=503)
        except Exception as e:
            logging.error(f"Error proxying to pred_tracker: {e}")
            return Response(f"Error connecting to Pred Tracker: {str(e)}", status=500)

    return sock
