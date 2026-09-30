import logging
from dash_reports.sub_servers.pred_tracker.models import Event


class LoggingHandler:
    def __init__(self):
        pass

    async def handle_event(self, event: Event):
        prefix = {"LOW": "[L]", "MEDIUM": "[M]", "HIGH": "[H]", "CRITICAL": "[!]"}.get(event.severity, "[ ]")
        
        logging.warning(f"{prefix} [{event.platform.upper()}] [{event.event_type}] {event.market_question[:60]}")
        logging.warning(f"   └─ {', '.join(f'{k}: {v}' for k, v in event.details.items())}")
        logging.warning(f"   └─ {event.market_url}")
