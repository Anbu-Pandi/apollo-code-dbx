import asyncio
import logging
import os
import uvicorn
from dash_reports.sub_servers.pred_tracker.unified_tracker import UnifiedPredictionTracker
from dash_reports.sub_servers.pred_tracker.web.server import WebServer


def get_ssl_config():
    try:
        from apo_config.config import config
        cert_folder = config.getconfig('pred_tracker', 'certificate_dir')
        if cert_folder:
            cert_path = os.path.join(cert_folder, 'server.crt')
            key_path = os.path.join(cert_folder, 'server.key')
            if os.path.exists(cert_path) and os.path.exists(key_path):
                return cert_path, key_path
    except Exception as e:
        logging.debug(f"SSL config not available: {e}")
    return None, None


def main():
    try:
        from apo_config.config import required_config
        from common.utils.envsetup import init_app_using_parser, get_base_parser, kill_stale_server
        
        parser = get_base_parser("Unified Prediction Markets Tracker")
        parser.add_argument("--whale-threshold", type=float, default=100000)
        parser.add_argument("--host")
        parser.add_argument("--port", type=int)
        args = init_app_using_parser(parser)
        host = args.host or required_config("pred_tracker", "server.host")
        port = args.port or int(required_config("pred_tracker", "server.port"))

        kill_stale_server(port)
        
        cert_path, key_path = get_ssl_config()
        use_ssl = cert_path and key_path
        protocol = "https" if use_ssl else "http"
        
        logging.info("=" * 80)
        logging.info("UNIFIED PREDICTION MARKETS TRACKER")
        logging.info("Polymarket + Kalshi")
        logging.info("Web UI bind: %s://%s:%s", protocol, host, port)
        logging.info(f"Whale threshold: ${args.whale_threshold:,.0f}")
        logging.info("=" * 80)
        
        tracker = UnifiedPredictionTracker(whale_threshold=args.whale_threshold, min_liquidity=10000)
        web_server = WebServer(tracker)
        tracker.web_server = web_server
        
        async def run():
            config_kwargs = {
                "app": web_server.app,
                "host": host,
                "port": port,
                "log_level": "warning",
                "access_log": False
            }
            
            if use_ssl:
                config_kwargs["ssl_certfile"] = cert_path
                config_kwargs["ssl_keyfile"] = key_path
            
            server_task = asyncio.create_task(uvicorn.Server(uvicorn.Config(**config_kwargs)).serve())
            tracker_task = asyncio.create_task(tracker.run())
            await asyncio.gather(server_task, tracker_task)
        
        asyncio.run(run())
        
    except ImportError:
        from apo_config.config import required_config

        logging.basicConfig(level=logging.INFO)
        logging.info("UNIFIED PREDICTION MARKETS TRACKER")
        
        tracker = UnifiedPredictionTracker(whale_threshold=100000, min_liquidity=10000)
        web_server = WebServer(tracker)
        tracker.web_server = web_server
        
        async def run():
            host = required_config("pred_tracker", "server.host")
            port = int(required_config("pred_tracker", "server.port"))
            server_task = asyncio.create_task(
                uvicorn.Server(uvicorn.Config(
                    web_server.app,
                    host=host,
                    port=port,
                    log_level="warning",
                    access_log=False,
                )).serve()
            )
            tracker_task = asyncio.create_task(tracker.run())
            await asyncio.gather(server_task, tracker_task)
        
        asyncio.run(run())


if __name__ == "__main__":
    main()
