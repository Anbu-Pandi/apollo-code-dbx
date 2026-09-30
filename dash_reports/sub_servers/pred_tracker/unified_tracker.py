import asyncio
import logging
from dash_reports.sub_servers.pred_tracker.storage.memory_store import MemoryStore
from dash_reports.sub_servers.pred_tracker.core.event_bus import EventBus
from dash_reports.sub_servers.pred_tracker.handlers import StorageHandler, LoggingHandler
from dash_reports.sub_servers.pred_tracker.poly.api.async_events_client import AsyncPolymarketEventsClient
from dash_reports.sub_servers.pred_tracker.kalshi.api.async_client import AsyncKalshiClient
from dash_reports.sub_servers.pred_tracker.detectors.whale_detector import WhaleDetector
from dash_reports.sub_servers.pred_tracker.detectors.price_movement_detector import PriceMovementDetector


class UnifiedPredictionTracker:
    def __init__(self, whale_threshold: float = 100000, min_liquidity: float = 10000):
        self.whale_threshold = whale_threshold
        self.min_liquidity = min_liquidity
        self.store = MemoryStore()
        self.event_bus = EventBus()
        self.web_server = None
        
        self.poly_client = None
        self.kalshi_client = None
        
        self.whale_detector = WhaleDetector(self.store, whale_threshold)
        self.price_detector = PriceMovementDetector()
        
        storage_handler = StorageHandler(self.store)
        logging_handler = LoggingHandler()
        
        self.event_bus.subscribe("*", storage_handler.handle_event)
        self.event_bus.subscribe("*", logging_handler.handle_event)
        
    async def initialize(self):
        logging.info("Initializing Unified Prediction Markets Tracker...")
        
        self.poly_client = AsyncPolymarketEventsClient(self.min_liquidity)
        await self.poly_client.__aenter__()
        
        self.kalshi_client = AsyncKalshiClient(self.min_liquidity)
        await self.kalshi_client.__aenter__()
        
        self.whale_detector.set_clients(self.kalshi_client, self.poly_client)
        
        async def on_progress(platform: str, markets_so_far: list, page: int, is_final: bool):
            for market in markets_so_far:
                self.store.add_market(market)
            
            if self.web_server:
                await self.web_server.broadcast_market_update(platform)
            
            if is_final and platform == "kalshi":
                if self.web_server and self.web_server.analyst:
                    poly_count = len(self.store.get_all_markets(platform="polymarket"))
                    kalshi_count = len(self.store.get_all_markets(platform="kalshi"))
                    logging.info(f"All markets loaded: {poly_count} Polymarket + {kalshi_count} Kalshi")
                    asyncio.create_task(self.web_server.analyst.trigger_analysis())
        
        poly_markets = await self.poly_client.get_events()
        for market in poly_markets:
            self.store.add_market(market)
        
        if self.web_server:
            await self.web_server.broadcast_market_update("polymarket")
        
        await self.kalshi_client.get_markets(progress_callback=on_progress)
        
        all_markets = self.store.get_all_markets()
        self.price_detector.warm_up(all_markets)
        
    async def update_markets(self):
        while True:
            try:
                poly_markets = await self.poly_client.get_events()
                kalshi_markets = await self.kalshi_client.get_markets()
                
                for market in poly_markets + kalshi_markets:
                    self.store.add_market(market)
                
                if self.web_server:
                    await self.web_server.broadcast_market_update("polymarket")
                    await self.web_server.broadcast_market_update("kalshi")
                
                for market in self.store.get_markets_by_liquidity(platform="polymarket") + self.store.get_markets_by_liquidity(platform="kalshi"):
                    for event in await self.price_detector.detect(market):
                        await self.event_bus.publish(event)
                        
                self.store.cleanup()
                
            except Exception as e:
                logging.error(f"Error updating markets: {e}")
            
            await asyncio.sleep(30)
    
    async def monitor_whale_trades(self, platform: str):
        client = self.poly_client if platform == "polymarket" else self.kalshi_client
        logging.info(f"{platform}: Monitoring ALL trades for whales (>= ${self.whale_threshold:,.0f})")
        
        try:
            async for trade in client.stream_whale_trades(min_amount=self.whale_threshold, interval=15):
                self.store.add_trade(trade)
                self.store.update_user(platform, trade.user_address, trade.amount)
                
                for event in await self.whale_detector.detect(trade):
                    await self.event_bus.publish(event)
                    
        except Exception as e:
            logging.error(f"Error monitoring {platform} whale trades: {e}")
    
    async def run(self):
        await self.initialize()
        
        tasks = [
            self.event_bus.process_events(),
            self.update_markets(),
            self.monitor_whale_trades("polymarket"),
            self.monitor_whale_trades("kalshi")
        ]
        
        if self.web_server and self.web_server.analyst:
            tasks.append(self.web_server.analyst.start())
        
        logging.info("Alert systems active: Whale trades, Price movements, AI Analysis")
        
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logging.info("Shutting down...")
        finally:
            if self.web_server and self.web_server.analyst:
                await self.web_server.analyst.stop()
            if self.poly_client and self.poly_client.session:
                await self.poly_client.session.close()
            if self.kalshi_client and self.kalshi_client.session:
                await self.kalshi_client.session.close()
