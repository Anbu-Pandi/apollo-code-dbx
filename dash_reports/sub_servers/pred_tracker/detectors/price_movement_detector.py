import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict
from collections import defaultdict
from dash_reports.sub_servers.pred_tracker.models import Market, Event, CRYPTO_CATEGORIES


class PriceMovementDetector:
    MAX_ALERTS_PER_CATEGORY_PER_HOUR = 3
    MAX_CRYPTO_PER_PLATFORM_PER_HOUR = 1
    
    STOCK_PRICE_TAGS = {"stocks", "equities", "multi-strikes", "weekly", "up-or-down"}
    STOCK_PRICE_SERIES = {"multi-strikes-weekly", "above-below"}
    STOCK_PRICE_PATTERN = re.compile(
        r"(above|below|finish.*above|hit|close.*above|close.*below)\s*\$?\d+|(for the week|weekly.*price|week of|price at|price range|price.*on.*\d{4}|closes.*at|price\s+(?:up|down)\s+in\s+next)",
        re.IGNORECASE
    )
    KALSHI_INTRADAY_SERIES = {"kxinxu", "kxndxu", "kxdowu", "kxrutu", "kxbtc", "kxbtcd", "kxethd"}
    
    COOLDOWN_1H = 3600
    REDELTA_24H = 0.10
    
    def __init__(self, threshold_1h: float = 0.20, threshold_24h: float = 0.25,
                 skip_stock_price_bets: bool = True, stock_price_max_hours: int = 168):
        self.threshold_1h = threshold_1h
        self.threshold_24h = threshold_24h
        self.skip_stock_price_bets = skip_stock_price_bets
        self.stock_price_max_hours = stock_price_max_hours
        self.last_alert: Dict[str, datetime] = {}
        self.active_24h: Dict[str, float] = {}
        self.category_alerts: Dict[str, List[datetime]] = defaultdict(list)
        self.crypto_alerts_by_platform: Dict[str, List[datetime]] = defaultdict(list)
    def warm_up(self, markets: List[Market]):
        count = 0
        for market in markets:
            if market.last_price >= 0.98 or market.last_price <= 0.02:
                continue
            if abs(market.one_day_change) >= self.threshold_24h:
                self.active_24h[market.id] = market.one_day_change
                self.last_alert[market.id] = datetime.now()
                count += 1
        logging.info(f"Price detector warm-up: {count} markets already above 24H threshold, suppressed")
        
    def _check_category_limit(self, category: str, platform: str = None) -> bool:
        cat = (category or "General").upper()
        now = datetime.now()
        cutoff = now - timedelta(hours=1)
        
        if cat.lower() in CRYPTO_CATEGORIES and platform:
            key = f"crypto:{platform}"
            self.crypto_alerts_by_platform[key] = [t for t in self.crypto_alerts_by_platform[key] if t > cutoff]
            if len(self.crypto_alerts_by_platform[key]) >= self.MAX_CRYPTO_PER_PLATFORM_PER_HOUR:
                return False
        
        self.category_alerts[cat] = [t for t in self.category_alerts[cat] if t > cutoff]
        return len(self.category_alerts[cat]) < self.MAX_ALERTS_PER_CATEGORY_PER_HOUR
    
    def _record_category_alert(self, category: str, platform: str = None):
        cat = (category or "General").upper()
        self.category_alerts[cat].append(datetime.now())
        if cat.lower() in CRYPTO_CATEGORIES and platform:
            self.crypto_alerts_by_platform[f"crypto:{platform}"].append(datetime.now())
    
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
        
    async def detect(self, market: Market) -> List[Event]:
        if market.last_price >= 0.98 or market.last_price <= 0.02:
            self.active_24h.pop(market.id, None)
            return []
        
        if self.skip_stock_price_bets and self._is_short_term_stock_price(market):
            return []
        
        now = datetime.now()
        
        change_val = None
        event_type = None
        severity = None
        
        if abs(market.one_hour_change) >= self.threshold_1h:
            change_val = market.one_hour_change
            event_type = "PRICE_MOVEMENT_1H"
            severity = "HIGH"
        elif abs(market.one_day_change) >= self.threshold_24h:
            change_val = market.one_day_change
            event_type = "PRICE_MOVEMENT_24H"
            severity = "MEDIUM"
        
        if not change_val:
            self.active_24h.pop(market.id, None)
            return []
        
        if event_type == "PRICE_MOVEMENT_1H":
            if market.id in self.last_alert and (now - self.last_alert[market.id]).total_seconds() < self.COOLDOWN_1H:
                return []
            self.active_24h.pop(market.id, None)
        else:
            prev_change = self.active_24h.get(market.id)
            if prev_change is not None and abs(change_val) - abs(prev_change) < self.REDELTA_24H:
                return []
            self.active_24h[market.id] = change_val
        
        if not self._check_category_limit(market.category, market.platform):
            return []
        
        prev_price = market.last_price - change_val
        if prev_price < 0 or prev_price > 1:
            return []
        
        self.last_alert[market.id] = now
        self._record_category_alert(market.category, market.platform)
        
        return [Event(
            timestamp=now,
            platform=market.platform,
            event_type=event_type,
            market_id=market.id,
            market_question=market.question,
            market_url=market.url,
            category=market.category,
            details={
                "change": f"{change_val*100:+.0f}%",
                "prev_price": f"{prev_price*100:.1f}%",
                "new_price": f"{market.last_price*100:.1f}%",
                "volume": f"${market.volume:,.0f}",
                "liquidity": f"${market.liquidity:,.0f}",
                "lead": f"{market.lead_outcome}: {market.last_price*100:.1f}%",
                "second": f"{market.second_outcome}: {market.second_price*100:.1f}%" if market.second_outcome else None
            },
            severity=severity
        )]
