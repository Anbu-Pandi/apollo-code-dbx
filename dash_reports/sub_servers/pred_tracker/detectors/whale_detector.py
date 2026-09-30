import re
from datetime import datetime, timedelta
from typing import List, Optional, Set, Dict
from collections import defaultdict
from dash_reports.sub_servers.pred_tracker.models import Trade, Event, Market, CRYPTO_CATEGORIES
from dash_reports.sub_servers.pred_tracker.storage.memory_store import MemoryStore


class WhaleDetector:
    MAX_ALERTS_PER_CATEGORY_PER_HOUR = 3
    MAX_CRYPTO_PER_PLATFORM_PER_HOUR = 1
    
    STOCK_PRICE_TAGS = {"stocks", "equities", "multi-strikes", "weekly", "up-or-down"}
    STOCK_PRICE_SERIES = {"multi-strikes-weekly", "above-below"}
    STOCK_PRICE_PATTERN = re.compile(
        r"(above|below|finish.*above|hit|close.*above|close.*below)\s*\$?\d+|(for the week|weekly.*price|week of|price at|price range|price.*on.*\d{4}|closes.*at|price\s+(?:up|down)\s+in\s+next)",
        re.IGNORECASE
    )
    KALSHI_INTRADAY_SERIES = {"kxinxu", "kxndxu", "kxdowu", "kxrutu", "kxbtc", "kxbtcd", "kxethd"}
    
    def __init__(self, store: MemoryStore, threshold: float = 100000,
                 skip_stock_price_bets: bool = True, stock_price_max_hours: int = 168):
        self.store = store
        self.threshold = threshold
        self.skip_stock_price_bets = skip_stock_price_bets
        self.stock_price_max_hours = stock_price_max_hours
        self.last_check = {}
        self._kalshi_client = None
        self._poly_client = None
        self._allowed_categories: Set[str] = set()
        self._category_alerts: Dict[str, List[datetime]] = defaultdict(list)
        self._crypto_alerts_by_platform: Dict[str, List[datetime]] = defaultdict(list)
    
    def set_clients(self, kalshi_client, poly_client):
        self._kalshi_client = kalshi_client
        self._poly_client = poly_client
        if kalshi_client:
            self._allowed_categories.update(c.upper() for c in kalshi_client.ALLOWED_CATEGORIES)
        if poly_client:
            self._allowed_categories.update(t.upper() for t in poly_client.ALLOWED_TAGS)
    
    def _check_category_limit(self, category: str, platform: str = None) -> bool:
        cat = (category or "General").upper()
        now = datetime.now()
        cutoff = now - timedelta(hours=1)
        
        if cat.lower() in CRYPTO_CATEGORIES and platform:
            key = f"crypto:{platform}"
            self._crypto_alerts_by_platform[key] = [t for t in self._crypto_alerts_by_platform[key] if t > cutoff]
            if len(self._crypto_alerts_by_platform[key]) >= self.MAX_CRYPTO_PER_PLATFORM_PER_HOUR:
                return False
        
        self._category_alerts[cat] = [t for t in self._category_alerts[cat] if t > cutoff]
        return len(self._category_alerts[cat]) < self.MAX_ALERTS_PER_CATEGORY_PER_HOUR
    
    def _record_category_alert(self, category: str, platform: str = None):
        cat = (category or "General").upper()
        self._category_alerts[cat].append(datetime.now())
        if cat.lower() in CRYPTO_CATEGORIES and platform:
            self._crypto_alerts_by_platform[f"crypto:{platform}"].append(datetime.now())
    
    def _is_short_term_stock_price(self, market: Market) -> bool:
        hours_to_expiry = (market.end_date.replace(tzinfo=None) - datetime.now()).total_seconds() / 3600
        if hours_to_expiry > self.stock_price_max_hours:
            return False
        
        if market.platform == "polymarket":
            if market.tags and self.STOCK_PRICE_TAGS & set(market.tags):
                return True
            if market.series_ticker:
                for pattern in self.STOCK_PRICE_SERIES:
                    if pattern in market.series_ticker.lower():
                        return True
        
        if market.platform == "kalshi":
            ticker_prefix = market.id.split('-')[0].lower() if market.id else ""
            if ticker_prefix in self.KALSHI_INTRADAY_SERIES:
                return True
        
        if self.STOCK_PRICE_PATTERN.search(market.question):
            cat = (market.category or "").upper()
            if cat in {"STOCKS", "EQUITIES", "FINANCIALS", "FINANCE"}:
                return True
            if cat.lower() in CRYPTO_CATEGORIES:
                return True
        
        return False
        
    async def _fetch_market(self, trade: Trade) -> Optional[Market]:
        if trade.platform == "kalshi" and self._kalshi_client:
            return await self._kalshi_client.get_single_market(trade.market_id)
        elif trade.platform == "polymarket" and self._poly_client:
            return await self._poly_client.get_single_market(trade.market_id)
        return None
        
    async def detect(self, trade: Trade) -> List[Event]:
        if trade.amount < self.threshold:
            return []
        
        market = self.store.get_market(trade.market_id)
        
        if not market:
            market = await self._fetch_market(trade)
            if market:
                self.store.add_market(market)
        
        if market and (market.last_price >= 0.98 or market.last_price <= 0.02):
            return []
        
        if market and self.skip_stock_price_bets and self._is_short_term_stock_price(market):
            return []
        
        if self._allowed_categories:
            market_cat = (market.category or "").upper() if market else ""
            if not market_cat or market_cat not in self._allowed_categories:
                return []
        
        if market and (market.category or "").lower() in CRYPTO_CATEGORIES:
            top_id = self.store.get_top_crypto_market_id(market.platform)
            if not top_id or market.id != top_id:
                return []
        
        if not self._check_category_limit(market.category if market else None, trade.platform):
            return []
        
        key = f"{trade.market_id}:{trade.user_address}:{trade.timestamp.isoformat()}"
        if key in self.last_check:
            return []
        self.last_check[key] = datetime.now()
        self._record_category_alert(market.category if market else None, trade.platform)
        
        outcome_name = market.lead_outcome if market and market.lead_outcome not in ["Yes", "No"] else trade.outcome
        
        trade_side = (trade.side or "BUY").upper()
        if trade_side == "SELL" and outcome_name in ("Yes", "No"):
            outcome_name = "No" if outcome_name == "Yes" else "Yes"
        
        url = market.url if market else (trade.url or "")
        
        if trade.platform == "polymarket":
            if trade.profile_name:
                activity_url = f"https://polymarket.com/@{trade.profile_name}"
            else:
                activity_url = f"https://polymarket.com/profile/{trade.user_address}"
        else:
            activity_url = url
        
        return [Event(
            timestamp=datetime.now(),
            platform=trade.platform,
            event_type="WHALE_TRADE",
            market_id=trade.market_id,
            market_question=market.question if market else trade.market_id,
            market_url=url,
            category=market.category if market else None,
            details={
                "amount": f"${trade.amount:,.0f}",
                "candidate": outcome_name,
                "side": trade_side,
                "trade_time": trade.timestamp.isoformat(),
                "user": trade.display_name or (trade.user_address[:10] + "..."),
                "user_address": trade.user_address,
                "trade_id": trade.id,
                "activity_url": activity_url,
                "price": f"{trade.price * 100:.1f}%",
                "volume": f"${market.volume:,.0f}" if market else "N/A",
                "liquidity": f"${market.liquidity:,.0f}" if market else "N/A",
                "lead": f"{market.lead_outcome}: {market.last_price*100:.1f}%" if market else "N/A",
                "second": f"{market.second_outcome}: {market.second_price*100:.1f}%" if market and market.second_outcome else None
            },
            severity="HIGH"
        )]
