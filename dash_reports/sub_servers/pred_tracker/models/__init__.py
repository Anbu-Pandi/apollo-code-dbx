from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List


@dataclass
class MarketOption:
    outcome: str
    price: float
    change: float = 0.0


@dataclass
class Market:
    id: str
    platform: str
    question: str
    volume: float
    liquidity: float
    end_date: datetime
    url: str
    last_price: float
    category: str = ""
    open_interest: float = 0.0
    one_day_change: float = 0.0
    one_hour_change: float = 0.0
    lead_outcome: str = "Yes"
    second_outcome: str = "No"
    second_price: float = 0.0
    last_updated: Optional[datetime] = None
    is_multi_option: bool = False
    options: Optional[List[MarketOption]] = None
    event_group: Optional[str] = None
    tags: Optional[List[str]] = None
    series_ticker: Optional[str] = None
    
    
@dataclass
class Trade:
    id: str
    platform: str
    market_id: str
    user_address: str
    amount: float
    outcome: str
    price: float
    timestamp: datetime
    url: Optional[str] = None
    side: Optional[str] = None
    display_name: Optional[str] = None
    profile_name: Optional[str] = None
    

@dataclass
class User:
    address: str
    platform: str
    first_seen: datetime
    total_volume: float
    trade_count: int
    

@dataclass
class Event:
    timestamp: datetime
    platform: str
    event_type: str
    market_id: str
    market_question: str
    market_url: str
    details: dict
    severity: str
    category: Optional[str] = None


CRYPTO_CATEGORIES = {
    "crypto", "bitcoin", "ethereum", "solana", "altcoins", "memecoins",
    "crypto prices", "cryptocurrency", "ltc", "litecoin", "xrp", "ripple",
    "dogecoin", "doge", "shiba", "token price", "nft", "nfts", "blockchain",
    "web3", "eth", "sol", "btc", "defi", "chainlink", "polkadot", "avalanche"
}

__all__ = ["Market", "MarketOption", "Trade", "User", "Event", "CRYPTO_CATEGORIES"]
