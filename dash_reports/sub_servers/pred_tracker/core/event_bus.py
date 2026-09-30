import asyncio
import logging
from typing import Dict, List, Callable, Awaitable
from dash_reports.sub_servers.pred_tracker.models import Event


class EventBus:
    def __init__(self):
        self.subscribers: Dict[str, List[Callable[[Event], Awaitable[None]]]] = {}
        self.queue = asyncio.Queue()
        
    def subscribe(self, event_type: str, handler: Callable[[Event], Awaitable[None]]):
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(handler)
        
    async def publish(self, event: Event):
        logging.info(f"[FireEvent] {event.platform.upper()}: {event.event_type} - {event.market_question[:60]}")
        await self.queue.put(event)
        
    async def process_events(self):
        while True:
            event = await self.queue.get()
            handlers = self.subscribers.get(event.event_type, []) + self.subscribers.get("*", [])
            await asyncio.gather(*[handler(event) for handler in handlers])
            self.queue.task_done()
