import logging
from typing import Dict, Any, Optional

from dash_reports.sub_servers.teams_server.client import get_client


class RequestStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"


class BackgroundRequestManager:
    def __init__(self):
        self.is_running = True

    @property
    def requests(self):
        return {}

    def start_worker(self):
        pass

    def stop_worker(self):
        pass

    def submit_request(self, question: str, session_id: str = "default", user_id: str = 'anonymous',
                       genie_type: Optional[str] = None, model: Optional[str] = None) -> str:
        client = get_client()
        return client.submit_query(question, session_id=session_id, user_id=user_id,
                                   source_system='ai_chat', genie_type=genie_type, model=model)

    def get_request_status(self, request_id: str) -> Optional[Dict[str, Any]]:
        client = get_client()
        return client.get_request_status(request_id)

    def cancel_request(self, request_id: str) -> bool:
        client = get_client()
        return client.cancel_request(request_id)

    def clear_conversation(self, session_id: str = 'default', user_id: str = None,
                           genie_type: Optional[str] = None, model: Optional[str] = None):
        client = get_client()
        client.clear_conversation(
            session_id,
            user_id=user_id,
            genie_type=genie_type,
            model=model,
        )

    def health_check(self):
        client = get_client()
        return client.health_check()


request_manager = BackgroundRequestManager()
