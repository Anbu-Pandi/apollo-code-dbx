import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Callable
from dash_reports.sub_servers.pred_tracker.genie.pred_tracker_genie import PredTrackerGenie
from dash_reports.sub_servers.pred_tracker.storage.memory_store import MemoryStore


class AnalystScheduler:
    
    def __init__(self, store: MemoryStore, update_interval_seconds: int = 3600, enable_history: bool = True):
        self.store = store
        self.update_interval = update_interval_seconds
        self.enable_history = enable_history
        self.genie = PredTrackerGenie()
        self.latest_analysis = {
            "timestamp": None,
            "analysis": "Analysis starting...",
            "success": False,
            "market_count": {"polymarket": 0, "kalshi": 0},
            "alert_count": 0
        }
        self.analysis_history = [] if enable_history else None
        self._running = False
        self._broadcast_callback = None
    
    def set_broadcast_callback(self, callback: Callable):
        self._broadcast_callback = callback
    
    async def start(self):
        self._running = True
        logging.info(f"[Analyst] Scheduler started (every {self.update_interval}s)")
        
        while self._running:
            await asyncio.sleep(self.update_interval)
            await self._run_analysis()
    
    async def trigger_analysis(self):
        await self._run_analysis()
    
    async def stop(self):
        self._running = False
        logging.info("[Analyst] Stopped")
    
    async def _run_analysis(self):
        try:
            logging.info("[Analyst] Running market analysis...")
            
            poly_markets = self.store.get_markets_by_liquidity(platform="polymarket")[:200] or []
            kalshi_markets = self.store.get_markets_by_liquidity(platform="kalshi")[:200] or []
            recent_alerts = self.store.get_recent_events(limit=50, max_age_hours=48) or []
            
            if not poly_markets and not kalshi_markets:
                logging.warning("[Analyst] No markets available yet - skipping analysis")
                self.latest_analysis = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "analysis": "Waiting for markets to load...",
                    "success": False,
                    "market_count": {"polymarket": 0, "kalshi": 0},
                    "alert_count": 0
                }
                return
            
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self.genie.analyze_markets,
                poly_markets,
                kalshi_markets,
                recent_alerts,
                self.analysis_history if self.enable_history else None
            )
            
            self.latest_analysis = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "analysis": result.get("analysis", "No analysis"),
                "success": result.get("success", False),
                "market_count": result.get("market_count", {"polymarket": 0, "kalshi": 0}),
                "alert_count": result.get("alert_count", 0)
            }
            
            if self.enable_history and self.analysis_history is not None:
                self.analysis_history.append({
                    "timestamp": self.latest_analysis["timestamp"],
                    "analysis": self.latest_analysis["analysis"]
                })
                if len(self.analysis_history) > 5:
                    self.analysis_history.pop(0)
            
            logging.info(f"[Analyst] Analysis complete - {result['market_count']} markets analyzed")
            
            if self._broadcast_callback:
                await self._broadcast_callback(self.latest_analysis)
            
        except Exception as e:
            logging.error(f"[!] Analysis failed: {e}")
            self.latest_analysis = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "analysis": f"Analysis error: {str(e)}",
                "success": False,
                "market_count": {"polymarket": 0, "kalshi": 0},
                "alert_count": 0
            }
            
            if self._broadcast_callback:
                await self._broadcast_callback(self.latest_analysis)
    
    def get_latest_analysis(self) -> Dict[str, Any]:
        return self.latest_analysis
