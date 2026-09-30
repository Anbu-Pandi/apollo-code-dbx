import asyncio
import json
import logging
from datetime import datetime
from typing import List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from dash_reports.report_common import theme
from dash_reports.sub_servers.pred_tracker.unified_tracker import UnifiedPredictionTracker
from dash_reports.sub_servers.pred_tracker.models import Event
from dash_reports.sub_servers.pred_tracker.genie.analyst_scheduler import AnalystScheduler
from dash_reports.sub_servers.pred_tracker.signals import SignalExtractor


class WebServer:
    def __init__(self, tracker: UnifiedPredictionTracker):
        self.app = FastAPI()
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        self.tracker = tracker
        self.connections: List[WebSocket] = []
        self.analyst = AnalystScheduler(tracker.store, update_interval_seconds=3600)
        self.signal_extractor = SignalExtractor()
        self._last_signals = []
        self._signal_update_interval = 30
        
        self.setup_routes()
        self.tracker.event_bus.subscribe("*", self.broadcast_event)
        self.analyst.set_broadcast_callback(self.broadcast_analysis)
        
        @self.app.on_event("startup")
        async def start_signal_loop():
            asyncio.create_task(self.signal_update_loop())
            asyncio.create_task(self.question_matcher_refresh_loop())
        
    def setup_routes(self):
        @self.app.get("/", response_class=HTMLResponse)
        async def get_ui():
            # Inject the shared dark/light theme kit just before </head>.
            return HTML_TEMPLATE.replace("</head>", theme.head_tags() + "\n</head>", 1)
            
        @self.app.get("/api/stats")
        async def get_stats():
            poly_count = len([m for m in self.tracker.store.markets.values() if m.platform == "polymarket"])
            kalshi_count = len([m for m in self.tracker.store.markets.values() if m.platform == "kalshi"])
            return {
                "polymarket": poly_count,
                "kalshi": kalshi_count,
                "total": len(self.tracker.store.markets),
                "events": len(self.tracker.store.events)
            }
        
        @self.app.get("/api/analysis")
        async def get_analysis():
            return self.analyst.get_latest_analysis()
        
        @self.app.post("/api/analysis/refresh")
        async def refresh_analysis():
            await self.analyst.trigger_analysis()
            return {"status": "Analysis triggered"}
        
        @self.app.get("/api/analysis/raw")
        async def get_raw_analysis():
            data = self.analyst.get_latest_analysis()
            return {"raw": data.get("analysis", ""), "timestamp": data.get("timestamp")}
        
        @self.app.get("/api/criteria")
        async def get_criteria():
            """Return current filter criteria from actual code constants"""
            from dash_reports.sub_servers.pred_tracker.kalshi.api.async_client import AsyncKalshiClient
            from dash_reports.sub_servers.pred_tracker.poly.api.async_events_client import AsyncPolymarketEventsClient
            
            return {
                "kalshi": {
                    "categories": sorted(AsyncKalshiClient.ALLOWED_CATEGORIES),
                    "finance_categories": ["Companies", "Financials", "Economics"],
                    "volume_min_finance": "$10,000",
                    "volume_min_other": "$100,000"
                },
                "polymarket": {
                    "categories": sorted(AsyncPolymarketEventsClient.ALLOWED_TAGS)[:20],  # Show top 20
                    "category_count": len(AsyncPolymarketEventsClient.ALLOWED_TAGS),
                    "finance_categories": sorted(AsyncPolymarketEventsClient.FINANCE_CATEGORIES),
                    "volume_min_finance": "$10,000",
                    "volume_min_other": "$100,000"
                }
            }
            
        @self.app.get("/api/events")
        async def get_events():
            events = self.tracker.store.get_recent_events(100)
            return [self.event_to_dict(e) for e in events]
            
        @self.app.get("/api/markets/{platform}")
        async def get_markets(platform: str):
            markets = self.tracker.store.get_markets_by_liquidity(platform=platform)
            return [self._market_to_dict(m) for m in markets]
        
        @self.app.get("/api/signals")
        async def get_signals():
            poly_markets = [m for m in self.tracker.store.markets.values() if m.platform == "polymarket"]
            kalshi_markets = [m for m in self.tracker.store.markets.values() if m.platform == "kalshi"]
            analysis = self.analyst.get_latest_analysis()
            analysis_text = analysis.get("analysis", "") if analysis.get("success") else ""
            
            signals = self.signal_extractor.get_all_signals(
                poly_markets=poly_markets,
                kalshi_markets=kalshi_markets,
                analysis_text=analysis_text
            )
            return {
                "signals": [s.to_dict() for s in signals],
                "count": len(signals),
                "timestamp": datetime.now().isoformat()
            }
        
        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            self.connections.append(websocket)
            logging.info("[WS] Client connected")
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                self.connections.remove(websocket)
                logging.info("[WS] Client disconnected")
    
    def _market_to_dict(self, m):
        data = {
            "id": m.id,
            "platform": m.platform,
            "question": m.question,
            "volume": m.volume,
            "liquidity": m.liquidity,
            "open_interest": m.open_interest,
            "url": m.url,
            "price": m.last_price,
            "category": m.category,
            "lead_outcome": m.lead_outcome,
            "second_outcome": m.second_outcome,
            "second_price": m.second_price,
            "one_day_change": m.one_day_change,
            "one_hour_change": m.one_hour_change,
            "last_updated": m.last_updated.isoformat() if m.last_updated else None,
            "is_multi_option": m.is_multi_option
        }
        if m.is_multi_option and m.options:
            data["options"] = [{"outcome": o.outcome, "price": o.price, "change": o.change} for o in m.options]
        return data
    
    async def broadcast_event(self, event: Event):
        if self.connections:
            message = json.dumps({
                "type": "alert",
                "data": self.event_to_dict(event)
            })
            await asyncio.gather(*[conn.send_text(message) for conn in self.connections], return_exceptions=True)
    
    async def broadcast_analysis(self, analysis: dict):
        if self.connections:
            message = json.dumps({
                "type": "analysis",
                "data": analysis
            })
            await asyncio.gather(*[conn.send_text(message) for conn in self.connections], return_exceptions=True)
            logging.info("[Analyst] Pushed analysis update to clients")
    
    async def broadcast_market_update(self, platform: str):
        if self.connections:
            markets = self.tracker.store.get_markets_by_liquidity(platform=platform)
            message = json.dumps({
                "type": "market_update",
                "platform": platform,
                "data": [self._market_to_dict(m) for m in markets]
            })
            await asyncio.gather(*[conn.send_text(message) for conn in self.connections], return_exceptions=True)
    
    async def broadcast_signals(self):
        if self.connections:
            poly_markets = [m for m in self.tracker.store.markets.values() if m.platform == "polymarket"]
            kalshi_markets = [m for m in self.tracker.store.markets.values() if m.platform == "kalshi"]
            analysis = self.analyst.get_latest_analysis()
            analysis_text = analysis.get("analysis", "") if analysis.get("success") else ""
            
            signals = self.signal_extractor.get_all_signals(
                poly_markets=poly_markets,
                kalshi_markets=kalshi_markets,
                analysis_text=analysis_text
            )
            
            old_ranks = {s["id"]: s["rank"] for s in self._last_signals}
            new_data = [s.to_dict() for s in signals]
            
            for sig in new_data:
                old_rank = old_ranks.get(sig["id"])
                if old_rank is not None:
                    sig["rank_change"] = old_rank - sig["rank"]
                else:
                    sig["rank_change"] = 0
                    sig["is_new"] = True
            
            self._last_signals = new_data
            
            message = json.dumps({
                "type": "signals",
                "data": {
                    "signals": new_data,
                    "count": len(new_data),
                    "timestamp": datetime.now().isoformat()
                }
            })
            await asyncio.gather(*[conn.send_text(message) for conn in self.connections], return_exceptions=True)
            logging.debug(f"[Signals] Pushed {len(signals)} signals to {len(self.connections)} clients")
    
    async def signal_update_loop(self):
        while True:
            await asyncio.sleep(self._signal_update_interval)
            try:
                await self.broadcast_signals()
            except Exception as e:
                logging.error(f"Signal broadcast error: {e}")
    
    async def question_matcher_refresh_loop(self):
        while True:
            await asyncio.sleep(3600)
            try:
                from dash_reports.sub_servers.pred_tracker.signals.question_matcher import get_question_matcher
                matcher = get_question_matcher()
                matcher.refresh_cache()
                logging.info("[QuestionMatcher] Hourly cache refresh complete")
            except Exception as e:
                logging.error(f"QuestionMatcher refresh error: {e}")
    
    def event_to_dict(self, event: Event) -> dict:
        return {
            "timestamp": event.timestamp.isoformat(),
            "platform": event.platform,
            "type": event.event_type,
            "question": event.market_question,
            "url": event.market_url,
            "details": event.details,
            "severity": event.severity,
            "category": event.category
        }


HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Prediction Markets Tracker</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; 
            background: #ffffff; 
            color: #1f2937;
            font-size: 11px; 
        }
        .header { 
            background: linear-gradient(135deg, #0d7d6e 0%, #0a5f54 100%); 
            color: #ffffff; 
            padding: 12px 20px; 
            position: sticky; 
            top: 0; 
            z-index: 100; 
            border-bottom: 2px solid #0a5f54;
            box-shadow: 0 2px 8px rgba(10, 95, 84, 0.15);
        }
        .header h1 { 
            font-size: 18px; 
            font-weight: 800; 
            letter-spacing: -0.5px;
            color: #ffffff;
            margin: 0;
        }
        .header-top { display: flex; justify-content: space-between; align-items: center; }
        .search-box { display: flex; align-items: center; gap: 8px; }
        .search-input { 
            padding: 10px 16px; 
            border: 1px solid #d1d5db;
            background: #ffffff;
            color: #1f2937;
            outline: none; 
            font-size: 12px; 
            width: 320px;
            transition: all 0.2s;
        }
        .search-input:focus { 
            border-color: #0d7d6e;
            box-shadow: 0 0 0 3px rgba(13, 125, 110, 0.1);
        }
        .stats { display: flex; gap: 10px; margin-top: 8px; }
        .stat { 
            background: rgba(255, 255, 255, 0.15);
            border: 1px solid rgba(255, 255, 255, 0.2);
            padding: 6px 12px;
            font-size: 9px; 
            transition: all 0.3s ease;
            color: #ffffff;
        }
        .stat:hover { 
            background: rgba(255, 255, 255, 0.25);
            transform: translateY(-1px);
        }
        .stat.pulse { animation: pulse 0.5s ease-in-out; }
        @keyframes pulse { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.05); background: rgba(255, 255, 255, 0.3); } }
        .stat-value { font-size: 16px; font-weight: 800; color: #ffffff; }
        
        .tabs { 
            display: flex; 
            background: #f9fafb;
            border-bottom: 2px solid #e5e7eb;
            position: sticky; 
            top: 62px; 
            z-index: 99;
        }
        .tab { 
            padding: 10px 20px; 
            cursor: pointer; 
            border-bottom: 3px solid transparent;
            font-size: 10px; 
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            transition: all 0.2s;
            color: #6b7280;
        }
        .tab:hover { 
            background: #ffffff;
            color: #1f2937;
        }
        .tab.active { 
            border-bottom-color: currentColor;
            background: #ffffff;
        }
        .tab.poly.active {
            color: #2E5CFF;
            border-bottom-color: #2E5CFF;
        }
        .tab.kalshi.active {
            color: #09C285;
            border-bottom-color: #09C285;
        }
        .tab.signals.active {
            color: #f59e0b;
            border-bottom-color: #f59e0b;
            background: linear-gradient(180deg, #fffbeb 0%, #ffffff 100%);
        }
        
        .container { max-width: 1600px; margin: 0 auto; padding: 12px; background: #f9fafb; }
        .section-title { 
            font-size: 12px; 
            font-weight: 800; 
            margin: 12px 0 8px;
            color: #111827;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        
        .alerts { 
            background: #ffffff;
            border: 1px solid #e5e7eb;
            padding: 10px; 
            margin-bottom: 12px; 
            max-height: 350px; 
            overflow-y: auto;
            flex: 1;
        }
        .alerts-analysis-container {
            display: flex;
            gap: 10px;
            margin-bottom: 12px;
        }
        .analysis-panel {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            padding: 12px;
            flex: 1;
            max-height: 350px;
            overflow-y: auto;
        }
        .analysis-header {
            font-size: 11px;
            font-weight: 800;
            color: #0d7d6e;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 1px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .analysis-timestamp {
            font-size: 9px;
            color: #6b7280;
            font-weight: 400;
            text-transform: none;
            letter-spacing: 0;
        }
        .analysis-content {
            font-size: 11px;
            line-height: 1.6;
            color: #374151;
            white-space: pre-wrap;
        }
        .analysis-loading {
            color: #9ca3af;
            font-style: italic;
        }
        .alert { 
            padding: 10px; 
            margin: 6px 0;
            font-size: 10px; 
            border-left: 3px solid;
            background: #f9fafb;
            transition: all 0.2s ease;
        }
        .alert:hover { 
            background: #f3f4f6;
            transform: translateX(3px);
            border-left-width: 4px;
        }
        .alert.HIGH { 
            background: #fef3c7;
            border-color: #f59e0b;
        }
        .alert.CRITICAL { 
            background: #fee2e2;
            border-color: #ef4444;
        }
        .alert-header { 
            font-weight: 700; 
            margin-bottom: 5px; 
            display: flex; 
            align-items: center; 
            gap: 5px;
            color: #111827;
        }
        .alert-details { color: #6b7280; font-size: 9px; margin: 3px 0; }
        
        .markets-grid { 
            display: grid; 
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); 
            gap: 12px;
            min-height: 200px;
        }
        .market-card { 
            background: #ffffff;
            border: 1px solid #e5e7eb;
            padding: 12px;
            transition: all 0.2s;
            cursor: pointer;
        }
        .market-card:hover { 
            border-color: currentColor;
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
        }
        #polymarket .market-card:hover {
            border-color: #2E5CFF;
            box-shadow: 0 4px 12px rgba(46, 92, 255, 0.1);
        }
        #kalshi .market-card:hover {
            border-color: #09C285;
            box-shadow: 0 4px 12px rgba(9, 194, 133, 0.1);
        }
        .market-question { 
            font-weight: 700; 
            font-size: 11px; 
            margin-bottom: 8px; 
            line-height: 1.4;
            color: #111827;
            text-decoration: none;
            display: block;
        }
        .market-question:hover {
            color: #0d7d6e;
        }
        .market-stats { 
            display: flex; 
            gap: 14px; 
            font-size: 9px; 
            color: #6b7280;
            margin-bottom: 10px;
        }
        .market-stat-value { 
            font-weight: 800;
            font-size: 11px;
        }
        #polymarket .market-stat-value {
            color: #2E5CFF;
        }
        #kalshi .market-stat-value {
            color: #09C285;
        }
        .candidates { 
            display: flex; 
            gap: 8px; 
            margin-top: 10px;
        }
        .candidate { 
            flex: 1; 
            text-align: center; 
            padding: 8px;
            font-size: 9px;
            background: #f9fafb;
            border: 1px solid #e5e7eb;
        }
        .candidate.first { 
            border-color: currentColor;
            background: rgba(0, 0, 0, 0.02);
        }
        #polymarket .candidate.first {
            border-color: #2E5CFF;
            background: rgba(46, 92, 255, 0.05);
        }
        #kalshi .candidate.first {
            border-color: #09C285;
            background: rgba(9, 194, 133, 0.05);
        }
        .candidate.second { 
            border-color: #d1d5db;
            background: #f9fafb;
        }
        .candidate-name { 
            font-weight: 800; 
            font-size: 10px; 
            margin: 3px 0;
            color: #111827;
        }
        .candidate-percent { 
            font-size: 14px; 
            font-weight: 900;
        }
        #polymarket .candidate-percent {
            color: #2E5CFF;
        }
        #kalshi .candidate-percent {
            color: #09C285;
        }
        .candidate.second .candidate-percent { 
            color: #6b7280;
        }
        .market-link { 
            display: none;
        }
        
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .empty-state { 
            text-align: center; 
            padding: 50px; 
            color: #9ca3af;
            font-size: 11px;
            font-weight: 600;
        }
        
        .signals-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
            gap: 16px;
            padding: 8px 0;
        }
        .signal-card {
            background: #ffffff;
            border: 2px solid #e5e7eb;
            padding: 16px;
            transition: all 0.2s;
            position: relative;
        }
        .signal-card:hover {
            transform: translateY(-3px);
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
        }
        .signal-card.arbitrage {
            border-color: #10b981;
            background: linear-gradient(135deg, #f0fdf4 0%, #ffffff 100%);
        }
        .signal-card.arbitrage:hover { border-color: #059669; }
        .signal-card.whale_follow {
            border-color: #f59e0b;
            background: linear-gradient(135deg, #fffbeb 0%, #ffffff 100%);
        }
        .signal-card.whale_follow:hover { border-color: #d97706; }
        .signal-card.mispricing {
            border-color: #6366f1;
            background: linear-gradient(135deg, #eef2ff 0%, #ffffff 100%);
        }
        .signal-card.mispricing:hover { border-color: #4f46e5; }
        .signal-card.contrarian {
            border-color: #ec4899;
            background: linear-gradient(135deg, #fdf2f8 0%, #ffffff 100%);
        }
        .signal-card.contrarian:hover { border-color: #db2777; }
        
        .signal-type-badge {
            position: absolute;
            top: -1px;
            right: -1px;
            padding: 4px 10px;
            font-size: 9px;
            font-weight: 800;
            letter-spacing: 0.5px;
            color: white;
        }
        .signal-type-badge.arbitrage { background: linear-gradient(135deg, #10b981, #059669); }
        .signal-type-badge.whale_follow { background: linear-gradient(135deg, #f59e0b, #d97706); }
        .signal-type-badge.mispricing { background: linear-gradient(135deg, #6366f1, #4f46e5); }
        .signal-type-badge.contrarian { background: linear-gradient(135deg, #ec4899, #db2777); }
        
        .signal-title {
            font-weight: 800;
            font-size: 13px;
            color: #111827;
            margin-bottom: 8px;
            padding-right: 80px;
            line-height: 1.4;
        }
        .signal-description {
            font-size: 11px;
            color: #6b7280;
            margin-bottom: 12px;
            line-height: 1.5;
        }
        .signal-metrics {
            display: flex;
            gap: 16px;
            margin-bottom: 14px;
            padding: 10px;
            background: rgba(0,0,0,0.03);
            border-radius: 4px;
        }
        .signal-metric {
            text-align: center;
        }
        .signal-metric-value {
            font-size: 18px;
            font-weight: 900;
        }
        .signal-metric-value.positive { color: #10b981; }
        .signal-metric-value.negative { color: #ef4444; }
        .signal-metric-label {
            font-size: 8px;
            color: #9ca3af;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .signal-actions {
            display: flex;
            gap: 8px;
        }
        .signal-btn {
            flex: 1;
            padding: 10px 16px;
            font-size: 11px;
            font-weight: 800;
            text-decoration: none;
            text-align: center;
            border-radius: 4px;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
        }
        .signal-btn.poly {
            background: linear-gradient(135deg, #2E5CFF, #1e40af);
            color: white;
        }
        .signal-btn.poly:hover { transform: scale(1.02); box-shadow: 0 4px 12px rgba(46, 92, 255, 0.4); }
        .signal-btn.kalshi {
            background: linear-gradient(135deg, #09C285, #059669);
            color: white;
        }
        .signal-btn.kalshi:hover { transform: scale(1.02); box-shadow: 0 4px 12px rgba(9, 194, 133, 0.4); }
        .signal-btn.disabled {
            background: #e5e7eb;
            color: #9ca3af;
            cursor: not-allowed;
        }
        
        .signal-spread {
            position: absolute;
            bottom: 16px;
            right: 16px;
            font-size: 24px;
            font-weight: 900;
            color: #10b981;
            opacity: 0.3;
        }
        
        .signal-rank {
            position: absolute;
            top: 12px;
            left: 12px;
            width: 28px;
            height: 28px;
            background: #111827;
            color: white;
            font-size: 12px;
            font-weight: 900;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 4px;
        }
        .signal-rank.top3 { background: linear-gradient(135deg, #f59e0b, #d97706); }
        
        .rank-change {
            position: absolute;
            top: 8px;
            left: 44px;
            font-size: 10px;
            font-weight: 800;
            padding: 2px 6px;
            border-radius: 3px;
            animation: fadeIn 0.5s ease;
        }
        .rank-change.up { background: #d1fae5; color: #059669; }
        .rank-change.down { background: #fee2e2; color: #dc2626; }
        .rank-change.new { background: #dbeafe; color: #2563eb; }
        
        @keyframes fadeIn { from { opacity: 0; transform: translateY(-5px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes rankUp { 0% { transform: translateY(20px); opacity: 0; } 100% { transform: translateY(0); opacity: 1; } }
        @keyframes rankDown { 0% { transform: translateY(-20px); opacity: 0; } 100% { transform: translateY(0); opacity: 1; } }
        
        .signal-card.rank-moved-up { animation: rankUp 0.4s ease-out; }
        .signal-card.rank-moved-down { animation: rankDown 0.4s ease-out; }
        .signal-card.is-new { animation: fadeIn 0.6s ease-out; border-width: 3px; }
        
        .math-toggle {
            background: none;
            border: 1px dashed #d1d5db;
            padding: 6px 12px;
            font-size: 9px;
            color: #6b7280;
            cursor: pointer;
            width: 100%;
            margin-top: 10px;
            transition: all 0.2s;
        }
        .math-toggle:hover { border-color: #9ca3af; color: #374151; background: #f9fafb; }
        
        .math-traces {
            display: none;
            margin-top: 12px;
            padding: 12px;
            background: #1f2937;
            border-radius: 6px;
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
            font-size: 10px;
        }
        .math-traces.expanded { display: block; }
        
        .math-trace {
            margin-bottom: 12px;
            padding-bottom: 10px;
            border-bottom: 1px solid #374151;
        }
        .math-trace:last-child { margin-bottom: 0; border-bottom: none; padding-bottom: 0; }
        
        .math-formula-name {
            color: #f59e0b;
            font-weight: 700;
            font-size: 9px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 4px;
        }
        .math-formula {
            color: #60a5fa;
            margin-bottom: 6px;
        }
        .math-inputs {
            color: #9ca3af;
            font-size: 9px;
            margin-bottom: 4px;
        }
        .math-calc {
            color: #a5f3fc;
            background: #0f172a;
            padding: 6px 8px;
            border-radius: 3px;
            margin: 4px 0;
        }
        .math-result {
            color: #4ade80;
            font-weight: 700;
            font-size: 12px;
        }
        
        .signal-volume {
            font-size: 9px;
            color: #9ca3af;
            margin-top: 8px;
            display: flex;
            gap: 12px;
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-top">
            <h1>PREDICTION MARKETS TRACKER</h1>
            <div class="search-box">
                <input type="text" class="search-input" id="searchInput" placeholder="Search markets, questions, outcomes, categories, platform (poly/kalshi)..." onkeyup="applySearch()">
            </div>
        </div>
        <div class="stats">
            <div class="stat" style="background: rgba(245, 158, 11, 0.3); border-color: rgba(245, 158, 11, 0.5);">
                <div class="stat-value" id="signalCount">0</div>
                <div>🎯 SIGNALS</div>
            </div>
            <div class="stat">
                <div class="stat-value" id="polyCount">0</div>
                <div>POLYMARKET</div>
            </div>
            <div class="stat">
                <div class="stat-value" id="kalshiCount">0</div>
                <div>KALSHI</div>
            </div>
            <div class="stat">
                <div class="stat-value" id="alertCount">0</div>
                <div>ALERTS</div>
            </div>
        </div>
    </div>
    
    <div class="container">
        <div class="section-title">LIVE ALERTS & AI ANALYSIS</div>
        <div class="alerts-analysis-container">
            <div class="alerts" id="alerts">
                <div class="empty-state">Waiting for alerts...</div>
            </div>
            <div class="analysis-panel">
                <div class="analysis-header">
                    AI SUMMARY
                    <span class="analysis-timestamp" id="analysisTimestamp"></span>
                </div>
                <div class="analysis-content" id="analysisContent">
                    <div class="analysis-loading">Analyzing markets...</div>
                </div>
            </div>
        </div>
        
        <div class="tabs">
            <div class="tab poly active" onclick="switchTab('polymarket')">POLYMARKET MARKETS</div>
            <div class="tab kalshi" onclick="switchTab('kalshi')">KALSHI MARKETS</div>
            <div class="tab signals" onclick="switchTab('signals')">🎯 SIGNALS</div>
        </div>
        
        <div id="polymarket" class="tab-content active">
            <div class="section-title">
                All Qualifying Markets
                <span style="font-size: 11px; color: #6b7280; font-weight: 400; margin-left: 8px;" id="polyCriteria">Loading criteria...</span>
            </div>
            <div class="markets-grid" id="polyMarkets"></div>
        </div>
        
        <div id="kalshi" class="tab-content">
            <div class="section-title">
                All Qualifying Markets
                <span style="font-size: 11px; color: #6b7280; font-weight: 400; margin-left: 8px;" id="kalshiCriteria">Loading criteria...</span>
            </div>
            <div class="markets-grid" id="kalshiMarkets"></div>
        </div>
        
        <div id="signals" class="tab-content">
            <div class="section-title" style="display: flex; align-items: center; gap: 12px;">
                🎯 ACTIONABLE SIGNALS
                <span style="font-size: 10px; color: #6b7280; font-weight: 400;">Click links to execute manually</span>
                <button onclick="loadSignals()" style="margin-left: auto; padding: 6px 12px; background: #f59e0b; color: white; border: none; font-size: 10px; font-weight: 700; cursor: pointer;">↻ REFRESH</button>
            </div>
            <div class="signals-grid" id="signalsGrid">
                <div class="empty-state">Loading signals...</div>
            </div>
        </div>
    </div>
    
    <script>
        let ws;
        let allMarkets = { polymarket: [], kalshi: [] };
        let allEvents = [];
        let allSignals = [];
        
        const NYC_TZ = 'America/New_York';
        function fmtTimeET(iso) {
            return new Date(iso).toLocaleTimeString('en-US', {
                timeZone: NYC_TZ, hour: 'numeric', minute: '2-digit', hour12: true
            }) + ' ET';
        }
        function fmtDateTimeET(iso) {
            return new Date(iso).toLocaleTimeString('en-US', {
                timeZone: NYC_TZ, month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', hour12: true
            }) + ' ET';
        }
        
        function switchTab(tabName) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            
            let tabClass = tabName;
            if (tabName === 'polymarket') tabClass = 'poly';
            else if (tabName === 'kalshi') tabClass = 'kalshi';
            
            document.querySelector(`.tab.${tabClass}`).classList.add('active');
            document.getElementById(tabName).classList.add('active');
            applySearch();
        }
        
        function applySearch() {
            const query = document.getElementById('searchInput').value.toLowerCase();
            // Filter markets
            renderMarkets('polymarket', query);
            renderMarkets('kalshi', query);
            renderEvents(query);
            // Filter alerts and analysis
            filterAlertsAndAnalysis(query);
        }
        
        function filterAlertsAndAnalysis(query) {
            if (!query) query = '';
            query = query.toLowerCase().trim();
            
            // Filter alerts
            const alertsContainer = document.getElementById('alerts');
            const allAlerts = alertsContainer.querySelectorAll('.alert');
            
            if (!query) {
                // Show all alerts
                allAlerts.forEach(alert => alert.style.display = '');
                // Show full analysis
                const analysisContent = document.getElementById('analysisContent');
                const originalHtml = analysisContent.getAttribute('data-original-html');
                if (originalHtml) {
                    analysisContent.innerHTML = originalHtml;
                }
                return;
            }
            
            // Filter each alert by its text content
            allAlerts.forEach(alert => {
                const text = alert.textContent.toLowerCase();
                alert.style.display = text.includes(query) ? '' : 'none';
            });
            
            // Filter AI analysis lines
            const analysisContent = document.getElementById('analysisContent');
            const originalHtml = analysisContent.getAttribute('data-original-html');
            
            if (originalHtml) {
                // Parse and filter lines
                const tempDiv = document.createElement('div');
                tempDiv.innerHTML = originalHtml;
                
                const lines = tempDiv.querySelectorAll('li, div');
                lines.forEach(line => {
                    const text = line.textContent.toLowerCase();
                    line.style.display = text.includes(query) ? '' : 'none';
                });
                
                analysisContent.innerHTML = tempDiv.innerHTML;
            }
        }
        
        // Fuzzy search with typo tolerance (Levenshtein distance)
        function levenshteinDistance(a, b) {
            const matrix = [];
            
            for (let i = 0; i <= b.length; i++) {
                matrix[i] = [i];
            }
            
            for (let j = 0; j <= a.length; j++) {
                matrix[0][j] = j;
            }
            
            for (let i = 1; i <= b.length; i++) {
                for (let j = 1; j <= a.length; j++) {
                    if (b.charAt(i - 1) === a.charAt(j - 1)) {
                        matrix[i][j] = matrix[i - 1][j - 1];
                    } else {
                        matrix[i][j] = Math.min(
                            matrix[i - 1][j - 1] + 1,
                            matrix[i][j - 1] + 1,
                            matrix[i - 1][j] + 1
                        );
                    }
                }
            }
            
            return matrix[b.length][a.length];
        }
        
        function fuzzyMatch(text, query) {
            if (!query) return true;
            
            text = text.toLowerCase();
            query = query.toLowerCase();
            
            // Exact substring match
            if (text.includes(query)) return true;
            
            // Check each word in text
            const words = text.split(/\s+/);
            for (const word of words) {
                // Allow 1-2 character difference based on word length
                const threshold = query.length <= 4 ? 1 : 2;
                const distance = levenshteinDistance(word, query);
                
                if (distance <= threshold) return true;
                
                // Partial word match for longer queries
                if (query.length >= 4 && word.includes(query.substring(0, query.length - 1))) {
                    return true;
                }
            }
            
            return false;
        }
        
        function matchesSearch(text, query) {
            if (!query) return true;
            return fuzzyMatch(text, query);
        }
        
        async function loadStats() {
            const resp = await fetch(`${BASE_PATH}/api/stats`);
            const data = await resp.json();
            document.getElementById('polyCount').textContent = data.polymarket;
            document.getElementById('kalshiCount').textContent = data.kalshi;
            document.getElementById('alertCount').textContent = data.events;
            
            // Pulse animation on update
            document.getElementById('alertCount').parentElement.classList.add('pulse');
            setTimeout(() => {
                document.getElementById('alertCount').parentElement.classList.remove('pulse');
            }, 500);
        }
        
        async function loadAnalysis() {
            try {
                const resp = await fetch(`${BASE_PATH}/api/analysis`);
                const data = await resp.json();
                
                const content = document.getElementById('analysisContent');
                const timestamp = document.getElementById('analysisTimestamp');
                
                if (data.success) {
                    const formattedHtml = formatAnalysis(data.analysis);
                    content.innerHTML = formattedHtml;
                    // Store original for filtering
                    content.setAttribute('data-original-html', formattedHtml);
                } else {
                    content.innerHTML = `<div class="analysis-loading">${data.analysis}</div>`;
                }
                
                if (data.timestamp) {
                    timestamp.textContent = `Updated ${fmtDateTimeET(data.timestamp)}`;
                }
            } catch (e) {
                console.error('Failed to load analysis:', e);
            }
        }
        
        function formatAnalysis(text) {
            if (!text) return '<div class="analysis-loading">No analysis available</div>';
            
            // Get short site name from URL
            function getSiteName(url) {
                try {
                    const hostname = new URL(url).hostname.replace('www.', '');
                    const siteMap = {
                        'polymarket.com': 'POLY',
                        'kalshi.com': 'KALSHI',
                        'bloomberg.com': 'BBG',
                        'reuters.com': 'RTRS',
                        'wsj.com': 'WSJ',
                        'ft.com': 'FT',
                        'cnbc.com': 'CNBC',
                        'nytimes.com': 'NYT',
                        'washingtonpost.com': 'WAPO',
                        'forbes.com': 'FORBES',
                        'bbc.com': 'BBC',
                        'theguardian.com': 'GUARDIAN',
                        'economist.com': 'ECON',
                        'marketwatch.com': 'MW',
                        'businessinsider.com': 'BI',
                        'yahoo.com': 'YAHOO',
                        'cnn.com': 'CNN',
                        'apnews.com': 'AP',
                        'news.google.com': 'GNEWS'
                    };
                    for (const [domain, short] of Object.entries(siteMap)) {
                        if (hostname.includes(domain.replace('www.', ''))) return short;
                    }
                    return hostname.split('.')[0].toUpperCase().slice(0, 8);
                } catch { return 'LINK'; }
            }
            
            // Convert CITE[N|URL|SOURCE|TITLE] to clickable [N] links with source + title tooltip
            text = text.replace(/CITE\[(\d+)\|([^|\]]+)\|?([^|\]]*)\|?([^\]]*)\]/g, function(match, num, url, source, title) {
                const sourceName = source || getSiteName(url);
                const articleTitle = title || 'Article';
                return '<a href="' + url + '" target="_blank" title="[' + sourceName + '] ' + articleTitle + '" style="color: #0066cc; text-decoration: none; font-weight: 700;">[' + num + ']</a>';
            });
            
            // Convert markdown links [text](url) to HTML
            function convertMarkdownLinks(str) {
                return str.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function(match, text, url) {
                    if (match.includes('<a ')) return match;
                    
                    const site = getSiteName(url);
                    const tooltip = '[' + site + '] ' + text.replace(/\[POLY\]/g, '').replace(/\[KALSHI\]/g, '').trim();
                    
                    const isActivityLink = text.toLowerCase() === 'activity' || url.includes('/activity');
                    if (isActivityLink) {
                        return '<a href="' + url + '" target="_blank" title="View whale trades & activity" style="background: linear-gradient(135deg, #f59e0b, #d97706); color: white; padding: 2px 6px; font-size: 8px; font-weight: 700; border-radius: 3px; text-decoration: none; margin-left: 4px;">📊 ACTIVITY</a>';
                    }
                    
                    const hasPolyTag = text.includes('[POLY]');
                    const hasKalshiTag = text.includes('[KALSHI]');
                    
                    if (hasPolyTag || hasKalshiTag) {
                        const cleanText = text.replace(/\[POLY\]/g, '').replace(/\[KALSHI\]/g, '').trim();
                        let platformTag = '';
                        if (hasPolyTag || url.includes('polymarket.com')) {
                            platformTag = ' <span style="background: #2E5CFF; color: white; padding: 1px 4px; font-size: 7px; font-weight: 800; border-radius: 2px;">[POLY]</span>';
                        } else if (hasKalshiTag || url.includes('kalshi.com')) {
                            platformTag = ' <span style="background: #09C285; color: white; padding: 1px 4px; font-size: 7px; font-weight: 800; border-radius: 2px;">[KALSHI]</span>';
                        }
                        return '<a href="' + url + '" target="_blank" title="' + tooltip + '" style="color: #0066cc; text-decoration: underline;">' + cleanText + platformTag + '</a>';
                    }
                    
                    let platformTag = '';
                    if (url.includes('polymarket.com')) {
                        platformTag = ' <span style="background: #2E5CFF; color: white; padding: 1px 4px; font-size: 7px; font-weight: 800; border-radius: 2px;">[POLY]</span>';
                    } else if (url.includes('kalshi.com')) {
                        platformTag = ' <span style="background: #09C285; color: white; padding: 1px 4px; font-size: 7px; font-weight: 800; border-radius: 2px;">[KALSHI]</span>';
                    }
                    return '<a href="' + url + '" target="_blank" title="' + tooltip + '" style="color: #0066cc; text-decoration: underline;">' + text + platformTag + '</a>';
                });
            }
            
            // Convert bold **text** to HTML
            function convertBold(str) {
                return str.replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>');
            }
            
            let html = '';
            const lines = text.split(/\\n|\\r\\n|\\r/);
            let inList = false;
            
            for (let line of lines) {
                line = line.trim();
                if (!line) continue;
                
                // Convert markdown links and bold
                line = convertMarkdownLinks(line);
                line = convertBold(line);
                
                // Main header (# )
                if (line.startsWith('# ')) {
                    if (inList) {
                        html += '</ul>';
                        inList = false;
                    }
                    const headerText = line.substring(2);
                    html += `<div style="font-weight: 900; font-size: 14px; color: #0d7d6e; margin: 0 0 16px 0; padding-bottom: 8px; border-bottom: 2px solid #0d7d6e;">${headerText}</div>`;
                }
                // Section headers (##, ###, or ##\u00a0N.)
                else if (line.match(/^##[#\\s]/)) {
                    if (inList) {
                        html += '</ul>';
                        inList = false;
                    }
                    const headerText = line.replace(/^##[#\\s]+/, '');
                    html += `<div style="font-weight: 800; font-size: 12px; color: #0d7d6e; margin: 20px 0 10px 0; text-transform: uppercase; letter-spacing: 0.5px;">${headerText}</div>`;
                }
                // Bullet points (•, -, *, or starts with **)
                else if (line.match(/^[•\\-*]\\s/) || line.startsWith('**')) {
                    if (!inList) {
                        html += '<ul style="margin: 8px 0; padding-left: 20px; list-style-type: disc;">';
                        inList = true;
                    }
                    let bulletText = line.replace(/^[•\\-*]\\s*/, '');
                    
                    // Extract and render category tag at start: [CATEGORY]
                    const categoryMatch = bulletText.match(/^\[([^\]]+)\]\s*/);
                    if (categoryMatch) {
                        const category = categoryMatch[1];
                        const colors = getCategoryColor(category);
                        const categoryBadge = `<span style="display: inline-block; padding: 2px 6px; background: ${colors.bg}; color: ${colors.text}; font-size: 8px; font-weight: 700; border-radius: 3px; margin-right: 6px;">${category.toUpperCase()}</span>`;
                        bulletText = categoryBadge + bulletText.substring(categoryMatch[0].length);
                    }
                    
                    html += `<li style="margin: 6px 0; line-height: 1.6; color: #374151;">${bulletText}</li>`;
                }
                // Regular text
                else {
                    if (inList) {
                        html += '</ul>';
                        inList = false;
                    }
                    html += `<div style="margin: 8px 0; line-height: 1.6; color: #374151;">${line}</div>`;
                }
            }
            
            if (inList) {
                html += '</ul>';
            }
            
            return html || '<div class="analysis-loading">No insights found</div>';
        }
        
        function getCategoryColor(category) {
            const colors = {
                'POLITICS': { bg: '#dbeafe', text: '#1e40af' },      // Blue
                'GEOPOLITICS': { bg: '#dbeafe', text: '#1e40af' },   // Blue
                'WORLD': { bg: '#dbeafe', text: '#1e40af' },         // Blue
                'CRYPTO': { bg: '#fef3c7', text: '#92400e' },        // Amber
                'BITCOIN': { bg: '#fef3c7', text: '#92400e' },       // Amber
                'CRYPTOCURRENCY': { bg: '#fef3c7', text: '#92400e' },// Amber
                'SPORTS': { bg: '#d1fae5', text: '#065f46' },        // Green
                'COMPANIES': { bg: '#e0e7ff', text: '#3730a3' },     // Indigo
                'EARNINGS': { bg: '#e0e7ff', text: '#3730a3' },      // Indigo
                'FINANCE': { bg: '#ddd6fe', text: '#5b21b6' },       // Purple
                'FINANCIALS': { bg: '#ddd6fe', text: '#5b21b6' },    // Purple
                'ECONOMICS': { bg: '#ddd6fe', text: '#5b21b6' },     // Purple
                'ENTERTAINMENT': { bg: '#fce7f3', text: '#9f1239' }, // Pink
                'TECHNOLOGY': { bg: '#99f6e4', text: '#0f766e' },    // Teal
                'AI': { bg: '#99f6e4', text: '#0f766e' },            // Teal
                'SCIENCE': { bg: '#ccfbf1', text: '#115e59' },       // Teal
                'CLIMATE': { bg: '#bbf7d0', text: '#166534' },       // Green
                'CLIMATE AND WEATHER': { bg: '#bbf7d0', text: '#166534' }, // Green
                'POP CULTURE': { bg: '#fce7f3', text: '#9f1239' },   // Pink
                'DEFAULT': { bg: '#f3f4f6', text: '#6b7280' }        // Gray
            };
            
            const cat = category ? category.toUpperCase() : 'DEFAULT';
            return colors[cat] || colors['DEFAULT'];
        }
        
        const BASE_PATH = window.location.pathname.replace(/\/$/, '');
        
        async function loadSignals() {
            try {
                const resp = await fetch(`${BASE_PATH}/api/signals`);
                if (!resp.ok) {
                    console.error(`Failed to load signals: ${resp.status}`);
                    return;
                }
                const data = await resp.json();
                allSignals = data.signals || [];
                console.log(`Loaded ${allSignals.length} signals`);
                renderSignals(false);
            } catch (err) {
                console.error('Error loading signals:', err);
            }
        }
        
        function renderSignals(animate = false) {
            const container = document.getElementById('signalsGrid');
            container.innerHTML = '';
            
            document.getElementById('signalCount').textContent = allSignals.length;
            
            if (allSignals.length === 0) {
                container.innerHTML = '<div class="empty-state">No actionable signals detected. Check back after the next AI analysis.</div>';
                return;
            }
            
            allSignals.forEach((sig, idx) => {
                const card = document.createElement('div');
                card.className = `signal-card ${sig.type}`;
                card.id = `signal-${sig.id}`;
                
                if (animate) {
                    if (sig.is_new) card.classList.add('is-new');
                    else if (sig.rank_change > 0) card.classList.add('rank-moved-up');
                    else if (sig.rank_change < 0) card.classList.add('rank-moved-down');
                }
                
                const typeLabels = {
                    'arbitrage': '⚡ ARBITRAGE',
                    'whale_follow': '🐋 WHALE FOLLOW',
                    'mispricing': '📊 MISPRICING',
                    'contrarian': '🎲 CONTRARIAN',
                    'momentum': '📈 MOMENTUM'
                };
                
                const evClass = sig.ev_percent > 0 ? 'positive' : 'negative';
                const evSign = sig.ev_percent > 0 ? '+' : '';
                
                const rankClass = sig.rank <= 3 ? 'top3' : '';
                let rankChangeHtml = '';
                if (animate && sig.rank_change > 0) {
                    rankChangeHtml = `<div class="rank-change up">▲${sig.rank_change}</div>`;
                } else if (animate && sig.rank_change < 0) {
                    rankChangeHtml = `<div class="rank-change down">▼${Math.abs(sig.rank_change)}</div>`;
                } else if (animate && sig.is_new) {
                    rankChangeHtml = `<div class="rank-change new">NEW</div>`;
                }
                
                const polyBtn = sig.poly_url 
                    ? `<a href="${sig.poly_url}" target="_blank" class="signal-btn poly">
                         <span>POLY</span>
                         <span style="font-size: 14px; font-weight: 900;">${sig.poly_price ? (sig.poly_price * 100).toFixed(0) + '%' : 'VIEW'}</span>
                       </a>`
                    : `<span class="signal-btn disabled">POLY N/A</span>`;
                
                const kalshiBtn = sig.kalshi_url 
                    ? `<a href="${sig.kalshi_url}" target="_blank" class="signal-btn kalshi">
                         <span>KALSHI</span>
                         <span style="font-size: 14px; font-weight: 900;">${sig.kalshi_price ? (sig.kalshi_price * 100).toFixed(0) + '%' : 'VIEW'}</span>
                       </a>`
                    : `<span class="signal-btn disabled">KALSHI N/A</span>`;
                
                const spreadHtml = sig.spread ? `<div class="signal-spread">${sig.spread.toFixed(0)}%</div>` : '';
                
                const categoryColors = getCategoryColor(sig.category);
                const categoryBadge = `<span style="display: inline-block; padding: 2px 6px; background: ${categoryColors.bg}; color: ${categoryColors.text}; font-size: 8px; font-weight: 700; border-radius: 3px; margin-left: 40px; margin-bottom: 6px;">${sig.category}</span>`;
                
                let mathTracesHtml = '';
                if (sig.math && sig.math.length > 0) {
                    const tracesContent = sig.math.map(t => `
                        <div class="math-trace">
                            <div class="math-formula-name">${t.formula}</div>
                            <div class="math-formula">${t.latex}</div>
                            <div class="math-inputs">Inputs: ${Object.entries(t.inputs).map(([k,v]) => `${k}=${typeof v === 'number' ? v.toFixed(3) : v}`).join(', ')}</div>
                            <div class="math-calc">${t.calc}</div>
                            <div class="math-result">= ${t.result.toFixed(4)}</div>
                        </div>
                    `).join('');
                    
                    mathTracesHtml = `
                        <button class="math-toggle" onclick="toggleMath('${sig.id}')">📐 SHOW MATH (${sig.math.length} formulas)</button>
                        <div class="math-traces" id="math-${sig.id}">
                            ${tracesContent}
                        </div>
                    `;
                }
                
                const volumeHtml = (sig.volume || sig.liquidity) ? `
                    <div class="signal-volume">
                        ${sig.volume ? `<span>Vol: $${(sig.volume/1000).toFixed(0)}K</span>` : ''}
                        ${sig.liquidity ? `<span>Liq: $${(sig.liquidity/1000).toFixed(0)}K</span>` : ''}
                    </div>
                ` : '';
                
                card.innerHTML = `
                    <div class="signal-rank ${rankClass}">#${sig.rank}</div>
                    ${rankChangeHtml}
                    <div class="signal-type-badge ${sig.type}">${typeLabels[sig.type] || sig.type.toUpperCase()}</div>
                    ${categoryBadge}
                    <div class="signal-title" style="padding-left: 40px;">${sig.title}</div>
                    <div class="signal-description">${sig.description}</div>
                    <div class="signal-metrics">
                        <div class="signal-metric">
                            <div class="signal-metric-value ${evClass}">${evSign}${sig.ev_percent.toFixed(1)}%</div>
                            <div class="signal-metric-label">Expected Value</div>
                        </div>
                        <div class="signal-metric">
                            <div class="signal-metric-value">${sig.kelly_percent.toFixed(1)}%</div>
                            <div class="signal-metric-label">Kelly Size</div>
                        </div>
                        <div class="signal-metric">
                            <div class="signal-metric-value">${(sig.confidence * 100).toFixed(0)}%</div>
                            <div class="signal-metric-label">Confidence</div>
                        </div>
                        ${sig.spread ? `<div class="signal-metric">
                            <div class="signal-metric-value positive">${sig.spread.toFixed(1)}%</div>
                            <div class="signal-metric-label">Spread</div>
                        </div>` : ''}
                        ${sig.model_price ? `<div class="signal-metric">
                            <div class="signal-metric-value">${(sig.model_price * 100).toFixed(0)}%</div>
                            <div class="signal-metric-label">Model P</div>
                        </div>` : ''}
                    </div>
                    <div class="signal-actions">
                        ${polyBtn}
                        ${kalshiBtn}
                    </div>
                    ${volumeHtml}
                    ${mathTracesHtml}
                    ${spreadHtml}
                `;
                container.appendChild(card);
            });
        }
        
        function toggleMath(sigId) {
            const el = document.getElementById('math-' + sigId);
            if (el) {
                el.classList.toggle('expanded');
                const btn = el.previousElementSibling;
                if (el.classList.contains('expanded')) {
                    btn.textContent = '📐 HIDE MATH';
                } else {
                    const count = el.querySelectorAll('.math-trace').length;
                    btn.textContent = `📐 SHOW MATH (${count} formulas)`;
                }
            }
        }
        
        async function loadMarkets(platform) {
            try {
                const resp = await fetch(`${BASE_PATH}/api/markets/${platform}`);
                if (!resp.ok) {
                    console.error(`Failed to load ${platform} markets: ${resp.status}`);
                    return;
                }
                allMarkets[platform] = await resp.json();
                console.log(`Loaded ${allMarkets[platform].length} ${platform} markets`);
                renderMarkets(platform, '');
            } catch (err) {
                console.error(`Error loading ${platform} markets:`, err);
            }
        }
        
        function renderMarkets(platform, query) {
            const markets = allMarkets[platform] || [];
            const containerId = platform === 'polymarket' ? 'polyMarkets' : 'kalshiMarkets';
            const container = document.getElementById(containerId);
            container.innerHTML = '';
            
            const filtered = markets.filter(m => {
                if (!query) return true;
                
                // Build comprehensive search text
                const searchText = `${m.question} ${m.lead_outcome} ${m.second_outcome} ${m.category} ${m.platform}`;
                
                // Platform name aliases with fuzzy matching
                const normalizedQuery = query.toLowerCase();
                const platformAliases = m.platform === 'polymarket' ? 
                    ['poly', 'polymarket', 'polmarket', 'polimarket'] : 
                    ['kalshi', 'kalshi', 'kalshy', 'kalsi'];
                
                // Check platform matches with fuzzy
                for (const alias of platformAliases) {
                    if (fuzzyMatch(alias, normalizedQuery)) {
                        return true;
                    }
                }
                
                // Fuzzy search across all text
                return fuzzyMatch(searchText, normalizedQuery);
            });
            
            // Sort by LIQUIDITY DESC - current action, not historical! 💰
            // Volume filters out junk, liquidity shows what's trading NOW
            filtered.sort((a, b) => (b.liquidity || 0) - (a.liquidity || 0));
            
            if (filtered.length === 0) {
                container.innerHTML = '<div class="empty-state">No markets match your search</div>';
                return;
            }
            
            filtered.forEach(m => {
                const card = document.createElement('div');
                card.className = 'market-card';
                
                // Format last updated time
                let lastUpdated = '';
                if (m.last_updated) {
                    const dt = new Date(m.last_updated);
                    const now = new Date();
                    const diffMs = now - dt;
                    const diffMins = Math.floor(diffMs / 60000);
                    if (diffMins < 1) {
                        lastUpdated = 'Just now';
                    } else if (diffMins < 60) {
                        lastUpdated = `${diffMins}m ago`;
                    } else {
                        const diffHrs = Math.floor(diffMins / 60);
                        lastUpdated = `${diffHrs}h ago`;
                    }
                }
                
                card.className = 'market-card';
                
                const categoryColors = getCategoryColor(m.category);
                const categoryBadge = m.category ? 
                    `<div style="margin: 4px 0;"><span style="display: inline-block; padding: 2px 6px; background: ${categoryColors.bg}; color: ${categoryColors.text}; font-size: 8px; font-weight: 700; border-radius: 3px;">${m.category.toUpperCase()}</span></div>` 
                    : '';
                
                card.innerHTML = `
                    <a href="${m.url}" target="_blank" class="market-question">${m.question}</a>
                    ${categoryBadge}
                    <div class="market-stats">
                        <div><span class="market-stat-value">$${(m.liquidity || 0).toLocaleString()}</span> Liq</div>
                        <div><span class="market-stat-value">$${(m.open_interest || 0).toLocaleString()}</span> OI</div>
                        <div><span class="market-stat-value">$${(m.volume || 0).toLocaleString()}</span> Vol</div>
                        ${lastUpdated ? `<div style="color: #9ca3af; font-size: 9px;">${lastUpdated}</div>` : ''}
                    </div>
                    <div class="candidates">
                        <div class="candidate first">
                            <div style="font-size: 8px; color: ${m.platform === 'polymarket' ? '#2E5CFF' : '#09C285'}; font-weight: 800;">1ST</div>
                            <div class="candidate-name" style="color: #111827;">${m.lead_outcome}</div>
                            <div style="display: flex; align-items: center; gap: 4px;">
                                <div class="candidate-percent" style="color: ${m.platform === 'polymarket' ? '#2E5CFF' : '#09C285'};">${(m.price * 100).toFixed(0)}%</div>
                                ${m.one_hour_change !== undefined && m.one_hour_change !== 0 ? 
                                    `<span style="font-size: 9px; font-weight: 700; color: ${m.one_hour_change > 0 ? '#10b981' : '#ef4444'};">
                                        ${m.one_hour_change > 0 ? '+' : ''}${(m.one_hour_change * 100).toFixed(1)}%
                                    </span>` : ''}
                            </div>
                        </div>
                        <div class="candidate second">
                            <div style="font-size: 8px; color: #6b7280; font-weight: 800;">2ND</div>
                            <div class="candidate-name" style="color: #111827;">${m.second_outcome || 'No'}</div>
                            <div class="candidate-percent" style="color: #6b7280;">${((m.second_price || 0) * 100).toFixed(0)}%</div>
                        </div>
                    </div>
                `;
                container.appendChild(card);
            });
        }
        
        async function loadEvents() {
            const resp = await fetch(`${BASE_PATH}/api/events`);
            allEvents = await resp.json();
            renderEvents('');
        }
        
        function renderEvents(query) {
            const events = allEvents || [];
            const container = document.getElementById('alerts');
            
            if (events.length === 0) {
                container.innerHTML = '<div class="empty-state">Waiting for alerts...</div>';
                return;
            }
            
            const filtered = events.filter(e => {
                if (!query) return true;
                
                const normalizedQuery = query.toLowerCase();
                
                // Alert type aliases for better matching
                const alertTypeText = e.type.replace(/_/g, ' ').toLowerCase();
                const alertTypeAliases = [];
                
                if (e.type.includes('PRICE')) {
                    alertTypeAliases.push('price', 'price move', 'price movement', 'price change');
                }
                if (e.type.includes('WHALE')) {
                    alertTypeAliases.push('whale', 'whale trade', 'large trade', 'big trade');
                }
                if (e.type.includes('VOLUME')) {
                    alertTypeAliases.push('volume', 'volume spike', 'high volume');
                }
                if (e.type.includes('1H')) {
                    alertTypeAliases.push('1h', '1 hour', 'hourly');
                }
                if (e.type.includes('24H')) {
                    alertTypeAliases.push('24h', '24 hour', 'daily', 'day');
                }
                
                // Check alert type aliases
                for (const alias of alertTypeAliases) {
                    if (fuzzyMatch(alias, normalizedQuery)) {
                        return true;
                    }
                }
                
                // Platform name aliases with fuzzy matching
                const platformAliases = e.platform === 'polymarket' ? 
                    ['poly', 'polymarket', 'polmarket', 'polimarket'] : 
                    ['kalshi', 'kalshi', 'kalshy', 'kalsi'];
                
                // Check platform matches with fuzzy
                for (const alias of platformAliases) {
                    if (fuzzyMatch(alias, normalizedQuery)) {
                        return true;
                    }
                }
                
                // Build comprehensive search text
                const searchText = `${e.question} ${alertTypeText} ${e.platform} ${JSON.stringify(e.details)}`;
                
                // Fuzzy search across all text
                return fuzzyMatch(searchText, normalizedQuery);
            });
            
            if (filtered.length === 0) {
                container.innerHTML = '<div class="empty-state">No alerts match your search</div>';
                return;
            }
            
            // Sort by timestamp descending (newest first)
            filtered.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
            
            container.innerHTML = '';
            filtered.slice(0, 20).forEach(e => {
                const alert = document.createElement('div');
                alert.className = `alert ${e.severity}`;
                const platformName = e.platform === 'polymarket' ? 'POLY' : 'KALSHI';
                const platformBadge = `<span style="display: inline-block; padding: 2px 6px; background: ${e.platform === 'polymarket' ? '#2E5CFF' : '#09C285'}; color: white; font-size: 8px; font-weight: 800; letter-spacing: 0.5px;">${platformName}</span>`;
                
                // Add category badge with colors
                const categoryColors = getCategoryColor(e.category);
                const categoryBadge = e.category ? 
                    `<span style="display: inline-block; padding: 2px 6px; background: ${categoryColors.bg}; color: ${categoryColors.text}; font-size: 8px; font-weight: 700; margin-left: 4px;">${e.category.toUpperCase()}</span>` 
                    : '';
                
                // Format alert type for better readability - INCLUDE TIMEFRAME!
                const alertTypeFormatted = e.type.replace(/_/g, ' ').replace(/24H/g, '(24h)');
                let alertTypePrefix;
                if (e.type.includes('WHALE')) {
                    alertTypePrefix = 'WHALE TRADE';
                } else if (e.type.includes('PRICE')) {
                    const timeframe = e.type.includes('1H') ? '1H' : e.type.includes('24H') ? '24H' : '';
                    alertTypePrefix = timeframe ? `PRICE MOVE (${timeframe})` : 'PRICE MOVE';
                } else if (e.type.includes('VOLUME')) {
                    alertTypePrefix = 'VOLUME SPIKE';
                } else {
                    alertTypePrefix = 'ALERT';
                }
                
                const time = fmtTimeET(e.timestamp);
                
                // Format all metadata
                const metaItems = [];
                
                // Build a prominent outcome + delta line if we have price movement
                let mainOutcomeHtml = '';
                if (e.details.lead && e.details.change) {
                    const changeStr = e.details.change;
                    const changeValue = parseFloat(changeStr.replace(/[^0-9.-]/g, ''));
                    const isPositive = changeValue > 0;
                    const changeColor = isPositive ? '#10b981' : '#ef4444';
                    mainOutcomeHtml = `<div style="margin: 6px 0; padding: 8px; background: ${isPositive ? '#f0fdf4' : '#fef2f2'}; border-left: 3px solid ${changeColor}; font-weight: 700; font-size: 11px;">
                        ${e.details.lead} <span style="color: ${changeColor}; font-size: 12px;">${e.details.change}</span>
                    </div>`;
                } else if (e.details.change) {
                    // Just show change without outcome
                    const changeStr = e.details.change;
                    const changeValue = parseFloat(changeStr.replace(/[^0-9.-]/g, ''));
                    const isPositive = changeValue > 0;
                    const changeColor = isPositive ? '#10b981' : '#ef4444';
                    metaItems.push(`Δ <strong style="color: ${changeColor};">${e.details.change}</strong>`);
                }
                
                if (e.details.prev_price) metaItems.push(`Was: ${e.details.prev_price}`);
                if (e.details.new_price) metaItems.push(`Now: <strong>${e.details.new_price}</strong>`);
                if (e.type && e.type.includes('WHALE') && e.details.candidate) {
                    const outcome = e.details.candidate;
                    const tradeSide = (e.details.side || 'BUY').toUpperCase();
                    const isBuy = tradeSide === 'BUY';
                    const sideVerb = isBuy ? 'BOUGHT' : 'SOLD';
                    const sideColor = isBuy ? '#10b981' : '#ef4444';
                    const sideIcon = isBuy ? '🟢' : '🔴';
                    const entryPrice = e.details.price || '';
                    const tradeTime = e.details.trade_time ? fmtTimeET(e.details.trade_time) : '';
                    const userLabel = e.details.user || 'WHALE';
                    const activityLink = e.details.activity_url ? 
                        `<a href="${e.details.activity_url}" target="_blank" style="background: linear-gradient(135deg, #f59e0b, #d97706); color: white; padding: 2px 6px; font-size: 8px; font-weight: 700; border-radius: 3px; text-decoration: none;">📊 ${userLabel}</a>` :
                        `<span style="font-weight: 600;">${userLabel}</span>`;
                    
                    mainOutcomeHtml = `<div style="margin: 6px 0; padding: 8px; background: ${isBuy ? '#f0fdf4' : '#fef2f2'}; border-left: 3px solid ${sideColor}; font-size: 11px;">
                        <span style="background: ${sideColor}; color: white; padding: 2px 8px; font-size: 10px; font-weight: 800; border-radius: 3px;">${sideIcon} ${sideVerb} ${outcome.toUpperCase()}</span>
                        <span style="font-weight: 800; font-size: 13px; margin-left: 6px;">${e.details.amount}</span>
                        ${entryPrice ? '<span style="color: #6b7280; margin-left: 6px;">@ ' + entryPrice + '</span>' : ''}
                        ${tradeTime ? '<span style="color: #9ca3af; margin-left: 6px;">🕐 ' + tradeTime + '</span>' : ''}
                    </div>`;
                    metaItems.push(activityLink);
                    if (e.details.volume) metaItems.push(`Vol: ${e.details.volume}`);
                    if (e.details.liquidity) metaItems.push(`Liq: ${e.details.liquidity}`);
                } else {
                    if (e.details.candidate && e.details.amount) {
                        const outcome = (e.details.candidate || '').toUpperCase();
                        const isYes = outcome === 'YES';
                        const isNo = outcome === 'NO';
                        let sideColor = isYes ? '#10b981' : isNo ? '#ef4444' : '#6366f1';
                        let sideIcon = isYes ? '🟢' : isNo ? '🔴' : '🎯';
                        metaItems.push(`<span style="background: ${sideColor}; color: white; padding: 2px 8px; font-size: 9px; font-weight: 800; border-radius: 3px;">${sideIcon} ${outcome} ${e.details.amount}</span>`);
                    } else {
                        if (e.details.amount) metaItems.push(`<strong>${e.details.amount}</strong>`);
                    }
                    if (e.details.side) metaItems.push(`${e.details.side}`);
                    if (e.details.activity_url) {
                        const userLabel = e.details.user || 'WHALE';
                        metaItems.push(`<a href="${e.details.activity_url}" target="_blank" style="background: linear-gradient(135deg, #f59e0b, #d97706); color: white; padding: 2px 6px; font-size: 8px; font-weight: 700; border-radius: 3px; text-decoration: none;">📊 ${userLabel}</a>`);
                    } else if (e.details.user) {
                        metaItems.push(`User: ${e.details.user}`);
                    }
                    if (e.details.volume) metaItems.push(`Vol: ${e.details.volume}`);
                    if (e.details.liquidity) metaItems.push(`Liq: ${e.details.liquidity}`);
                }
                
                // Show second outcome if available (only for price alerts with outcomes)
                let secondOutcomeHtml = '';
                if (e.details.second && e.details.lead) {
                    secondOutcomeHtml = `<div style="margin-top: 4px; font-size: 10px; color: #6b7280;">2nd: ${e.details.second}</div>`;
                }
                
                alert.innerHTML = `
                    <div class="alert-header">
                        ${platformBadge}${categoryBadge} <strong>${alertTypePrefix}</strong>
                        <span style="float: right; color: #9ca3af; font-weight: normal;">${time}</span>
                    </div>
                    <div style="margin: 6px 0 8px 0; font-weight: 700; font-size: 11px; color: #111827; line-height: 1.4;">
                        <a href="${e.url}" target="_blank" style="color: #111827; text-decoration: none;">${e.question}</a>
                    </div>
                    ${mainOutcomeHtml}
                    ${secondOutcomeHtml}
                    <div class="alert-details">${metaItems.join(' • ')}</div>
                `;
                container.appendChild(alert);
            });
        }
        
        let wsRetries = 0;
        let pollInterval = null;
        
        function handleMessage(message) {
            if (message.type === 'alert') {
                allEvents.unshift(message.data);
                if (allEvents.length > 100) allEvents.pop();
                renderEvents(document.getElementById('searchInput').value.toLowerCase());
                loadStats();
                console.log(`[ALERT] ${message.data.platform.toUpperCase()}: ${message.data.type}`);
            } else if (message.type === 'market_update') {
                const platform = message.platform;
                const markets = message.data;
                allMarkets[platform] = markets;
                const query = document.getElementById('searchInput').value.toLowerCase();
                renderMarkets(platform, query);
                loadStats();
                console.log(`[MARKET_UPDATE] ${platform.toUpperCase()}: ${markets.length} markets updated`);
            } else if (message.type === 'analysis') {
                const data = message.data;
                const content = document.getElementById('analysisContent');
                const timestamp = document.getElementById('analysisTimestamp');
                
                if (data.success) {
                    const formattedHtml = formatAnalysis(data.analysis);
                    content.innerHTML = formattedHtml;
                    content.setAttribute('data-original-html', formattedHtml);
                } else {
                    content.innerHTML = `<div class="analysis-loading">${data.analysis}</div>`;
                }
                
                if (data.timestamp) {
                    timestamp.textContent = `Updated ${fmtDateTimeET(data.timestamp)}`;
                }
                console.log('[ANALYSIS] AI analysis update received - refreshing signals');
                loadSignals();
            } else if (message.type === 'markets_update') {
                allMarkets[message.platform] = message.data;
                renderMarkets(message.platform, document.getElementById('searchInput').value.toLowerCase());
                console.log(`[UPDATE] Markets refreshed: ${message.platform.toUpperCase()}`);
            } else if (message.type === 'signals') {
                const data = message.data;
                allSignals = data.signals || [];
                console.log(`[SIGNALS] Live update: ${allSignals.length} signals`);
                renderSignals(true);
                
                const movedUp = allSignals.filter(s => s.rank_change > 0).length;
                const movedDown = allSignals.filter(s => s.rank_change < 0).length;
                const newSignals = allSignals.filter(s => s.is_new).length;
                if (movedUp || movedDown || newSignals) {
                    console.log(`[SIGNALS] Rank changes: ▲${movedUp} ▼${movedDown} NEW:${newSignals}`);
                }
            }
        }
        
        function startPolling() {
            if (pollInterval) return;
            console.log('[POLLING] WebSocket unavailable, falling back to polling every 30s');
            document.title = '[POLL] Prediction Markets Tracker';
            pollInterval = setInterval(() => {
                loadStats();
                loadAnalysis();
                loadSignals();
                loadMarkets('polymarket');
                loadMarkets('kalshi');
                loadEvents();
            }, 30000);
        }
        
        function connectWebSocket() {
            const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${wsProtocol}//${window.location.host}${BASE_PATH}/ws`;
            console.log(`[WS] Connecting to: ${wsUrl}`);
            
            try {
                ws = new WebSocket(wsUrl);
            } catch (e) {
                console.error('[WS] Failed to create WebSocket:', e);
                startPolling();
                return;
            }
            
            ws.onopen = () => {
                console.log('[CONNECTED] WebSocket connected - Live updates active');
                document.title = '[LIVE] Prediction Markets Tracker';
                wsRetries = 0;
                if (pollInterval) {
                    clearInterval(pollInterval);
                    pollInterval = null;
                }
            };
            ws.onmessage = (event) => {
                const message = JSON.parse(event.data);
                handleMessage(message);
            };
            ws.onclose = (event) => {
                console.log(`[DISCONNECTED] WebSocket closed (code: ${event.code}, reason: ${event.reason || 'none'})`);
                document.title = '[OFFLINE] Prediction Markets Tracker';
                wsRetries++;
                if (wsRetries < 5) {
                    console.log(`[WS] Retry ${wsRetries}/5 in 3s...`);
                    setTimeout(connectWebSocket, 3000);
                } else {
                    console.log('[WS] Max retries reached, switching to polling mode');
                    startPolling();
                }
            };
            ws.onerror = (error) => {
                console.error('[ERROR] WebSocket error:', error);
            };
        }
        
        loadStats();
        loadAnalysis();
        loadSignals();
        loadMarkets('polymarket');
        loadMarkets('kalshi');
        loadEvents();
        loadCriteria();
        connectWebSocket();
        
        async function loadCriteria() {
            try {
                const resp = await fetch(`${BASE_PATH}/api/criteria`);
                const criteria = await resp.json();
                
                // Polymarket criteria
                const polyFinCats = criteria.polymarket.finance_categories.join(', ');
                const polyText = `(Vol: Finance/Business ${criteria.polymarket.volume_min_finance}+ | Other ${criteria.polymarket.volume_min_other}+)`;
                document.getElementById('polyCriteria').textContent = polyText;
                document.getElementById('polyCriteria').title = `Finance Categories (${criteria.polymarket.volume_min_finance}+): ${polyFinCats}\nOther Categories (${criteria.polymarket.volume_min_other}+): Politics, Crypto, Tech, etc.\nTotal Tracked Categories: ${criteria.polymarket.category_count}`;
                
                // Kalshi criteria
                const kalshiFinCats = criteria.kalshi.finance_categories.join(', ');
                const kalshiText = `(Vol: Finance/Business ${criteria.kalshi.volume_min_finance}+ | Other ${criteria.kalshi.volume_min_other}+)`;
                document.getElementById('kalshiCriteria').textContent = kalshiText;
                document.getElementById('kalshiCriteria').title = `Finance Categories (${criteria.kalshi.volume_min_finance}+): ${kalshiFinCats}\nOther Categories (${criteria.kalshi.volume_min_other}+): Politics, Crypto, Tech, etc.\nTotal Tracked Categories: ${criteria.kalshi.categories.length}`;
            } catch (e) {
                console.error('Failed to load criteria:', e);
            }
        }
    </script>
</body>
</html>
"""
