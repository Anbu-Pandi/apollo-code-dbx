from dash_reports.sub_servers.pred_tracker.models import Event
from dash_reports.sub_servers.pred_tracker.storage.memory_store import MemoryStore


class StorageHandler:
    def __init__(self, store: MemoryStore):
        self.store = store

    async def handle_event(self, event: Event):
        self.store.add_event(event)
