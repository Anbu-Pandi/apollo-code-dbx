import json
import logging
import asyncio
import traceback
from flask import Blueprint, request, Response, jsonify
from apo_config.config import config
from dash_reports import APP
from genai.common.config import Config

bot = None
adapter = None

def create_teams_bot_blueprint():
    api_bp = Blueprint('api', __name__, url_prefix='/api')

    @api_bp.route("/health", methods=["GET"])
    def bot_health():
        app_id = Config.TEAMS_APP_ID
        app_password = Config.TEAMS_APP_PASSWORD

        return jsonify({
            "status": "healthy",
            "bot": "Teams Bot on Dash",
            "env_app_id_set": bool(app_id),
            "env_app_id_preview": app_id[:8] + "..." if app_id and len(app_id) > 8 else app_id,
            "env_app_password_set": bool(app_password),
            "env_app_password_length": len(app_password) if app_password else 0
        })

    @api_bp.route("/bot/test-auth", methods=["GET"])
    def test_bot_auth():
        try:
            from botframework.connector.auth import MicrosoftAppCredentials
            tenant_id = Config.TEAMS_TENANT_ID

            credentials = MicrosoftAppCredentials(
                Config.TEAMS_APP_ID,
                Config.TEAMS_APP_PASSWORD,
                channel_auth_tenant=tenant_id
            )
            token = credentials.get_access_token()

            return jsonify({
                "status": "success",
                "message": "Authentication successful",
                "tenant_id": tenant_id,
                "token_length": len(token) if token else 0,
                "has_token": token is not None and len(token) > 0
            })
        except Exception as e:
            return jsonify({
                "status": "error",
                "error": str(e),
                "traceback": traceback.format_exc()
            }), 500

    @api_bp.route("/messages", methods=["POST"])
    def messages():
        global bot, adapter

        if bot is None or adapter is None:
            logging.error("Bot or adapter not initialized")
            return Response(status=500)

        if request.method == "POST":
            try:
                body = {'body': request.json}
                auth_header = request.headers.get("Authorization", "")
                logging.info(f"📨 Received message")

                async def call_bot():
                    return await adapter.process_activity(body, auth_header, bot.on_turn)

                asyncio.run(call_bot())
                logging.info("✅ Message processed successfully")
                return Response(status=200)
            except Exception as e:
                tb_str = traceback.format_exc()
                logging.error(f"❌ Error processing bot message: {e}\n{tb_str}")
                return Response(status=500)
        return Response(status=405)

    @api_bp.route("/messages_direct", methods=["POST"])
    def messages_direct():
        try:
            api_key = request.headers.get('Ocp-Apim-Subscription-Key')
            expected_key = config.getconfig(APP, 'api_key_messages_direct')

            if not api_key:
                logging.warning("Missing API key in messages_direct")
                return jsonify({"status": "error", "error": "Missing Ocp-Apim-Subscription-Key header"}), 401
            if api_key != expected_key:
                logging.warning("Invalid API key: %s...", api_key[:10])
                return jsonify({"status": "error", "error": "Invalid API key"}), 403

            data = request.json
            if not data:
                return jsonify({"status": "error", "error": "Request body is required"}), 400

            question = data.get('text') or data.get('question')
            if not question:
                return jsonify({"status": "error", "error": "Missing 'text' or 'question' field"}), 400

            user_id = data.get('user_id', 'api_user')
            mock_mode = data.get('mock', False)

            logging.info("Direct API - User: %s, Mock: %s, Question: %s", user_id, mock_mode, question[:100])

            from dash_reports.sub_servers.teams_server.client import get_client
            client = get_client()
            result = client.query_sync(
                question, session_id=user_id, user_id=user_id,
                source_system='direct_api', mock=mock_mode,
            )
            result['user_id'] = user_id

            logging.info("Direct API processed for %s", user_id)
            return jsonify(result), 200

        except Exception as e:
            logging.error("Error in messages_direct: %s\n%s", e, traceback.format_exc())
            return jsonify({"status": "error", "error": str(e)}), 500

    return api_bp

def initialize_teams_bot(is_debug_mode, get_port_func):
    global bot, adapter

    logging.info("Initializing Teams Bot...")

    try:
        from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings
        from botbuilder.core.conversation_state import ConversationState
        from genai.teams_bot.core.shared_storage import make_bot_storage
        from botframework.connector.auth import MicrosoftAppCredentials
        from genai.teams_bot.core.bot import TeamsGenieBot
        app_id = Config.TEAMS_APP_ID
        app_password = Config.TEAMS_APP_PASSWORD
        tenant_id = Config.TEAMS_TENANT_ID

        logging.info("Configuring bot with App ID: %s...", app_id[:8])
        logging.info("Password length: %d chars", len(app_password) if app_password else 0)
        logging.info("Tenant ID: %s", tenant_id)

        settings = BotFrameworkAdapterSettings(
            app_id=app_id,
            app_password=app_password,
            channel_auth_tenant=tenant_id
        )

        for url in [
            "https://smba.trafficmanager.net/amer/",
            "https://smba.trafficmanager.net/emea/",
            "https://smba.trafficmanager.net/apac/",
            "https://api.botframework.com",
        ]:
            MicrosoftAppCredentials.trust_service_url(url)

        if is_debug_mode:
            localhost_url = f"http://localhost:{get_port_func()}"
            MicrosoftAppCredentials.trust_service_url(localhost_url)
            logging.info("%s trusted for local development", localhost_url)

        adapter = BotFrameworkAdapter(settings)

        async def on_error(context, error):
            logging.error("[Bot Error] %s\n%s", error, traceback.format_exc())

        adapter.on_turn_error = on_error

        from dash_reports.sub_servers.teams_server.client import GenieFlightProxy
        genie_client = GenieFlightProxy()
        # Shared conversation state so the bot runs on every node (not per-process
        # MemoryStorage) -- see genai.teams_bot.core.shared_storage.
        conversation_state = ConversationState(make_bot_storage())
        bot = TeamsGenieBot(conversation_state, genie_client)
        bot._adapter = adapter

        # Start BYOT registration + polling loop (persistent, auto-reconnects)
        _start_byot_loop(genie_client)

        logging.info("Teams Bot initialized successfully")
        return True
    except Exception as e:
        logging.error("Teams Bot initialization failed: %s\n%s", e, traceback.format_exc())
        bot = None
        adapter = None
        return False


def _start_byot_loop(genie_client):
    """Start BYOT registration + polling using the shared AgentClient SDK."""
    from genai.teams_bot.core.teams_client_handler import TeamsClientHandler, TOOLS
    from flight_server.client_sdk.agent_client import AgentClient

    handler = TeamsClientHandler(bot, adapter, app_id=Config.TEAMS_APP_ID)
    if bot:
        bot._teams_handler = handler

    from flight_server.client_sdk.flight_transport import resolve_flight_server_endpoint
    host, port = resolve_flight_server_endpoint()

    import socket
    # Node-unique client_id so both nodes register as DISTINCT BYOT providers --
    # a shared "teams_bot" id would thrash on the flight registry. Shared
    # conversation refs (shared_storage) mean either provider can service a
    # proactive send, so N providers is correct, not duplicated.
    client = AgentClient(
        url=f"grpc+tls://{host}:{port}",
        client_id=f"teams_bot@{socket.gethostname().lower()}",
        client_type="teams_bot",
        user_name="",
        client_description="Teams bot — send messages, cards to users in Microsoft Teams",
    )
    for tool in TOOLS:
        client.register_tool_raw(tool, lambda method=tool["name"], **kw: handler.handle(method, kw))

    import threading
    threading.Timer(3.0, client.start).start()


def unwrap_api_endpoints_from_okta(server):
    logging.info("🔓 Unwrapping machine endpoints from Okta authentication...")

    okta_protected_endpoints = ['api.messages_direct']
    okta_protected_endpoints = []
    mcp_endpoints = {
        'health_check',
        'health_check_k8s',
        'readiness_check',
        'mcp_server_proxy',
        'mcp_server_help',
        'mcp_oauth_protected_resource_metadata',
        'mcp_oauth_authorization_server_metadata',
        'mcp_oauth_register',
        'mcp_oauth_authorize',
        'mcp_oauth_callback',
        'mcp_oauth_token',
    }
    wiki_endpoints = {
        'argus_wiki_proxy',
        'argus_wiki_unavailable',
    }

    for endpoint, view_func in list(server.view_functions.items()):
        if endpoint.startswith('api.') or endpoint in mcp_endpoints or endpoint in wiki_endpoints:
            if endpoint in okta_protected_endpoints:
                logging.info(f"🔒 Keeping Okta protection: {endpoint}")
                continue

            logging.info(f"📍 Unwrapping: {endpoint}")

            if (hasattr(view_func, '__closure__') and
                    view_func.__closure__ and
                    len(view_func.__closure__) >= 1):

                original_func = view_func.__closure__[0].cell_contents

                if callable(original_func):
                    server.view_functions[endpoint] = original_func
                    logging.info(f"   ✅ UNWRAPPED! Restored original function: {original_func}")
                else:
                    logging.info(f"   ⚠️ Cell 0 is not callable: {original_func}")
            else:
                logging.info(f"   ⚠️ No closure found")

    logging.info("✅ Machine endpoints unwrapped!")
