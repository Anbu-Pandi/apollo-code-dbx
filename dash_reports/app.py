# All startup toggles live in one place — see app_utils/runtime_flags.py.
from dash_reports.app_utils.runtime_flags import (
    HARDENED_TLS,
    IS_DEBUG_MODE,
    USE_OKTA,
    app_role,
    run_teams_bot,
)

from common.utils.envsetup import checkif_childprocess

if not checkif_childprocess(is_debug=IS_DEBUG_MODE):
    import logging

    from dash_reports.app_utils.prepare_app import prepare_app, get_port

    prepare_app(IS_DEBUG_MODE)

    from dash_reports.app_utils.app_factory import create_application
    from dash_reports.app_utils.server_config import ServerConfig

    from dash_reports.app_utils import app_callbacks  # noqa: F401

    logging.info("dash_reports starting: ARGUS_APP_ROLE=%s (run_teams_bot=%s)",
                 app_role(), run_teams_bot())
    app = create_application(USE_OKTA, IS_DEBUG_MODE, get_port, run_teams_bot=run_teams_bot())

if __name__ == "__main__":
    if not checkif_childprocess(is_debug=IS_DEBUG_MODE):
        from common.utils.envsetup import kill_stale_server
        from dash_reports.app_utils.custom_threading import initialize_custom_threading

        initialize_custom_threading()

        server_config = ServerConfig(IS_DEBUG_MODE, get_port)
        server_config.log_server_mode()

        server_kwargs = server_config.get_server_kwargs()
        kill_stale_server(int(server_kwargs.get('port', 8050)))

        # Hardened server only applies in direct-TLS mode; behind a
        # TLS-terminating proxy there's no ssl_context (plain HTTP loopback), so
        # we keep the stock run_server. See HARDENED_TLS in runtime_flags.py.
        _ssl_context = server_kwargs.get('ssl_context')
        if HARDENED_TLS and _ssl_context is not None:
            from dash_reports.app_utils.hardened_server import serve_hardened
            serve_hardened(app.server, server_kwargs['host'],
                           int(server_kwargs['port']), _ssl_context)
        else:
            app.run_server(**server_kwargs)
