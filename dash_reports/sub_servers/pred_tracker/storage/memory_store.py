from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dash_reports.sub_servers.pred_tracker.models import Market, Trade, User, Event, CRYPTO_CATEGORIES


class MemoryStore:
    def __init__(self):
        self.markets: Dict[str, Market] = {}
        self.trades: List[Trade] = []
        self.users: Dict[str, User] = {}
        self.events: List[Event] = []
        self.market_volumes: Dict[str, List[float]] = defaultdict(list)
        
    def add_market(self, market: Market):
        self.markets[market.id] = market
        
    def add_trade(self, trade: Trade):
        self.trades.append(trade)
        self.market_volumes[trade.market_id].append(trade.amount)
        
    def add_user(self, user: User):
        self.users[user.address] = user
        
    def update_user(self, platform: str, address: str, amount: float):
        key = f"{platform}:{address}"
        if key in self.users:
            self.users[key].total_volume += amount
            self.users[key].trade_count += 1
        else:
            self.users[key] = User(
                address=address,
                platform=platform,
                first_seen=datetime.now(),
                total_volume=amount,
                trade_count=1
            )
            
    def add_event(self, event: Event):
        self.events.append(event)
        
    def get_market(self, market_id: str) -> Optional[Market]:
        return self.markets.get(market_id)
    
    def get_user(self, platform: str, address: str) -> Optional[User]:
        return self.users.get(f"{platform}:{address}")
    
    def get_recent_events(self, limit: int = 50, max_age_hours: int = 24) -> List[Event]:
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        recent = [e for e in self.events if e.timestamp and e.timestamp > cutoff]
        return recent[-limit:]
    
    def get_top_markets_by_volume(self, limit: int = 20) -> List[Market]:
        return sorted(self.markets.values(), key=lambda m: m.volume, reverse=True)[:limit]
    
    def get_markets_by_liquidity(self, platform: str = None) -> List[Market]:
        markets = list(self.markets.values())
        if platform:
            markets = [m for m in markets if m.platform == platform]
        
        def should_keep(m):
            is_time_series = any(
                keyword in m.lead_outcome or keyword in (m.second_outcome or "")
                for keyword in ["Before ", "After ", "By ", "Through "]
            )
            if is_time_series:
                return True
            return 0.02 < m.last_price < 0.98
        
        filtered = [m for m in markets if should_keep(m)]
        sorted_markets = sorted(filtered, key=lambda m: m.liquidity, reverse=True)
        
        crypto_seen = False
        result = []
        for m in sorted_markets:
            if (m.category or "").lower() in CRYPTO_CATEGORIES:
                if crypto_seen:
                    continue
                crypto_seen = True
            result.append(m)
        return result
    
    def get_top_crypto_market_id(self, platform: str) -> Optional[str]:
        crypto_markets = [
            m for m in self.markets.values()
            if m.platform == platform
            and (m.category or "").lower() in CRYPTO_CATEGORIES
            and 0.02 < m.last_price < 0.98
        ]
        if not crypto_markets:
            return None
        return max(crypto_markets, key=lambda m: m.liquidity).id
    
    def cleanup(self, max_age_hours: int = 24):
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        self.trades = [t for t in self.trades if t.timestamp > cutoff]
        self.events = [e for e in self.events if e.timestamp and e.timestamp > cutoff]
        stale = {k for k in self.market_volumes if k not in self.markets}
        for k in stale:
            del self.market_volumes[k]
    
    def get_all_markets(self, platform: str = None) -> List[Market]:
        markets = list(self.markets.values())
        if platform:
            markets = [m for m in markets if m.platform == platform]
        return markets
