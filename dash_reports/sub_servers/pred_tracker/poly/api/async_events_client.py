import aiohttp
import asyncio
import ssl
import certifi
import logging
import re
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from collections import defaultdict
from dash_reports.sub_servers.pred_tracker.models import Market, Trade, MarketOption


class AsyncPolymarketEventsClient:
    BASE_URL = "https://gamma-api.polymarket.com"
    DATA_URL = "https://data-api.polymarket.com"
    
    ALLOWED_TAGS = {
        "Politics", "World", "Midterms", "Finance", "Economy", "Crypto", "Business", 
        "IPOs", "IPO", "Earnings", "Fed", "Fed Rates", "GDP", "Macro Indicators", 
        "Economic Policy", "Trade War", "Bitcoin", "Tech", "Big Tech", "AI", "OpenAI",
        "US Election", "Elections", "Trump", "Trump Presidency", "Geopolitics", 
        "Foreign Policy", "Global Elections", "Congress", "Courts",
        "Pre-Market", "Stocks", "Earnings Calls", "Business News"
    }
    
    FINANCE_CATEGORIES = {
        "finance", "economy", "business", "ipos", "earnings", 
        "gdp", "macro-indicators", "trade-war", "pre-market", "stocks"
    }
    
    FINANCE_TAGS = [
        "finance", "economy", "business", "earnings", "ipos", 
        "gdp", "stocks", "pre-market", "macro-indicators", "trade-war"
    ]
    
    OTHER_TAGS = [
        "politics", "crypto", "bitcoin", "tech", "big-tech", "ai",
        "elections", "trump", "trump-presidency", "geopolitics", "world"
    ]
    
    def __init__(self, min_volume: float = 100_000):
        self.session: Optional[aiohttp.ClientSession] = None
        self.min_volume = min_volume
        self._ssl_context = self._create_ssl_context()
        self._oi_cache: Dict[str, float] = {}
        
    def _create_ssl_context(self):
        try:
            return ssl.create_default_context(cafile=certifi.where())
        except Exception as e:
            logging.warning(f"SSL context fallback: {e}")
            return ssl.create_default_context()
    
    async def _fetch_open_interest(self, market_ids: List[str]) -> Dict[str, float]:
        if not market_ids:
            return {}
        
        oi_map = {}
        batch_size = 50
        
        for i in range(0, len(market_ids), batch_size):
            batch = market_ids[i:i + batch_size]
            try:
                params = [("market", mid) for mid in batch]
                async with self.session.get(
                    f"{self.DATA_URL}/oi",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for item in data:
                            market_id = item.get("market", "")
                            value = float(item.get("value", 0) or 0)
                            if market_id:
                                oi_map[market_id] = value
            except Exception as e:
                logging.warning(f"Polymarket OI fetch failed for batch: {e}")
        
        return oi_map
        
    async def __aenter__(self):
        connector = aiohttp.TCPConnector(ssl=self._ssl_context)
        self.session = aiohttp.ClientSession(connector=connector)
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
            
    async def get_events(self, limit_per_category: int = 100, active: bool = True) -> List[Market]:
        all_raw_events = {}
        
        logging.info(f"Polymarket: Fetching top {limit_per_category} per category...")
        
        for tag_slug in self.FINANCE_TAGS:
            await self._fetch_tag_events(tag_slug, limit_per_category, 10_000, active, all_raw_events)
        
        for tag_slug in self.OTHER_TAGS:
            await self._fetch_tag_events(tag_slug, limit_per_category, 100_000, active, all_raw_events)
        
        logging.info(f"Polymarket: Collected {len(all_raw_events)} unique events")
        
        all_condition_ids = []
        event_to_conditions = {}
        for event_id, event_data in all_raw_events.items():
            condition_ids = []
            for m in event_data.get("markets", []):
                cid = m.get("conditionId") or m.get("condition_id")
                if cid:
                    condition_ids.append(cid)
                    all_condition_ids.append(cid)
            event_to_conditions[event_id] = condition_ids
        
        oi_map = await self._fetch_open_interest(all_condition_ids)
        logging.info(f"Polymarket: Fetched OI for {len(oi_map)} markets")
        
        event_oi = {}
        for event_id, cids in event_to_conditions.items():
            event_oi[event_id] = sum(oi_map.get(cid, 0) for cid in cids)
        
        markets = []
        for event_id, event_data in all_raw_events.items():
            market = self._process_event(event_data, event_oi.get(event_id, 0))
            if market:
                markets.append(market)
        
        logging.info(f"Polymarket: Created {len(markets)} markets ({sum(1 for m in markets if m.is_multi_option)} multi-option)")
        return markets
    
    async def _fetch_tag_events(self, tag_slug: str, limit: int, volume_min: int, active: bool, events_dict: Dict):
        params = {
            "tag_slug": tag_slug,
            "limit": limit,
            "closed": "false" if active else "true",
            "archived": "false",
            "volume_min": volume_min,
            "order": "volume",
            "ascending": "false"
        }
        
        try:
            async with self.session.get(f"{self.BASE_URL}/events", params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()
            
            for event in data:
                event_id = event.get("id")
                if event_id and event_id not in events_dict:
                    tags = event.get("tags", [])
                    tag_labels = [t.get('label') or t.get('slug') if isinstance(t, dict) else str(t) for t in tags]
                    
                    if set(tag_labels) & self.ALLOWED_TAGS:
                        events_dict[event_id] = event
                        
        except Exception as e:
            logging.error(f"Error fetching tag '{tag_slug}': {e}")
    
    def _process_event(self, event: Dict, fetched_oi: float = 0) -> Optional[Market]:
        slug = event.get("slug", "")
        if not slug:
            return None
        
        title = event.get("title", "Unknown")
        markets_list = event.get("markets", [])
        
        tags = event.get("tags", [])
        tag_labels = [t.get('label') or t.get('slug') if isinstance(t, dict) else str(t) for t in tags]
        tag_slugs = [t.get('slug', '').lower() if isinstance(t, dict) else str(t).lower() for t in tags]
        category = next((t for t in tag_labels if t not in {'Earn 4%', 'World Elections', 'Global Elections'}), "General")
        
        series = event.get("series", [])
        series_ticker = series[0].get("ticker", "") if series else ""
        
        is_finance = bool(set(t.lower() for t in tag_labels) & self.FINANCE_CATEGORIES)
        volume = float(event.get("volume", 0))
        min_vol = 10_000 if is_finance else 100_000
        
        if volume < min_vol:
            return None
        
        liquidity = float(event.get("liquidityClob", 0) or event.get("liquidity", 0))
        open_interest = fetched_oi if fetched_oi > 0 else float(event.get("openInterest", 0) or 0)
        
        end_date_str = event.get("endDate", "2025-12-31")
        try:
            end_date_obj = datetime.fromisoformat(end_date_str.replace("Z", "+00:00") if "T" in end_date_str else end_date_str + "T00:00:00+00:00")
        except:
            end_date_obj = datetime(2025, 12, 31)
        
        options = self._extract_options(markets_list)
        
        if not options:
            return None
        
        options.sort(key=lambda x: x.price, reverse=True)
        
        if options[0].price >= 0.98 or options[0].price <= 0.02:
            return None
        
        is_multi = len(options) > 2 or (len(options) == 2 and options[0].outcome not in ["Yes", "No"])
        
        one_day_change = 0.0
        one_hour_change = 0.0
        lead_outcome_name = options[0].outcome
        
        for market in markets_list:
            group_title = market.get("groupItemTitle", "")
            outcomes_raw = market.get("outcomes", [])
            outcomes = eval(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
            first_outcome = outcomes[0] if outcomes else ""
            
            is_match = (group_title == lead_outcome_name or 
                       first_outcome == lead_outcome_name or
                       (first_outcome in ["Yes", "No"] and group_title == lead_outcome_name))
            
            if is_match or (not is_multi and markets_list.index(market) == 0):
                one_day_change = float(market.get("oneDayPriceChange", 0) or 0)
                one_hour_change = float(market.get("oneHourPriceChange", 0) or 0)
                break
        
        return Market(
            id=event.get("id", ""),
            platform="polymarket",
            question=title,
            volume=volume,
            liquidity=liquidity,
            end_date=end_date_obj,
            url=f"https://polymarket.com/event/{slug}",
            last_price=options[0].price,
            category=category,
            open_interest=open_interest,
            one_day_change=one_day_change,
            one_hour_change=one_hour_change,
            lead_outcome=options[0].outcome,
            second_outcome=options[1].outcome if len(options) > 1 else "No",
            second_price=options[1].price if len(options) > 1 else 0.0,
            last_updated=datetime.now(),
            is_multi_option=is_multi,
            options=options if is_multi else None,
            event_group=event.get("id"),
            tags=tag_slugs,
            series_ticker=series_ticker
        )
    
    def _extract_options(self, markets_list: List[Dict]) -> List[MarketOption]:
        outcome_dict = {}
        
        for market in markets_list:
            if market.get("closed") or market.get("archived"):
                continue
            
            outcome_prices_raw = market.get("outcomePrices")
            outcomes_raw = market.get("outcomes")
            sub_question = market.get("question", "")
            group_item_title = market.get("groupItemTitle")
            
            if not outcome_prices_raw or not outcomes_raw:
                continue
            
            try:
                prices = eval(outcome_prices_raw) if isinstance(outcome_prices_raw, str) else outcome_prices_raw
                outcomes = eval(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
            except:
                continue
            
            if len(outcomes) >= 1 and len(prices) >= 1:
                first_outcome = outcomes[0]
                yes_price = float(prices[0])
                
                if first_outcome in ["Yes", "No"] and sub_question:
                    if group_item_title:
                        outcome_name = group_item_title
                    else:
                        match = re.search(r'Will\s+(.+?)\s+(?:win|be|get)', sub_question, re.IGNORECASE)
                        if match:
                            outcome_name = match.group(1).strip()
                        else:
                            match = re.match(r'^([A-Z][A-Za-z\.\s]+?)(?:\s*[-:]|$)', sub_question)
                            outcome_name = match.group(1).strip() if match else sub_question
                else:
                    outcome_name = first_outcome
                
                if yes_price > 0.01 and outcome_name not in ["Yes", "No"]:
                    if outcome_name not in outcome_dict:
                        prev_price = float(market.get("previousPrice", yes_price) or yes_price)
                        outcome_dict[outcome_name] = MarketOption(
                            outcome=outcome_name,
                            price=yes_price,
                            change=yes_price - prev_price
                        )
        
        if outcome_dict:
            return list(outcome_dict.values())
        
        if markets_list:
            first = markets_list[0]
            prices_raw = first.get("outcomePrices", "[0.5]")
            try:
                prices = eval(prices_raw) if isinstance(prices_raw, str) else prices_raw
                yes_price = float(prices[0]) if prices else 0.5
                no_price = float(prices[1]) if len(prices) > 1 else 1.0 - yes_price
                
                prev_yes = float(first.get("previousPrice", yes_price) or yes_price)
                
                return [
                    MarketOption("Yes", yes_price, yes_price - prev_yes),
                    MarketOption("No", no_price, -(yes_price - prev_yes))
                ]
            except:
                return [MarketOption("Yes", 0.5, 0.0), MarketOption("No", 0.5, 0.0)]
        
        return []
    
    async def get_all_whale_trades(self, min_amount: float = 100000, limit: int = 100) -> list:
        params = {"limit": limit, "filterType": "cash", "filterAmount": int(min_amount)}
        try:
            async with self.session.get(f"{self.DATA_URL}/trades", params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception:
            return []
    
    async def get_single_market(self, slug: str) -> Optional[Market]:
        try:
            async with self.session.get(f"{self.BASE_URL}/events", params={"slug": slug}, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                resp.raise_for_status()
                events = await resp.json()
                if not events:
                    return None
                
                e = events[0]
                markets_list = e.get("markets", [])
                total_volume = sum(float(m.get("volume", 0)) for m in markets_list)
                total_liquidity = sum(float(m.get("liquidity", 0)) for m in markets_list)
                
                category = ""
                tags = e.get("tags", [])
                for tag in tags:
                    tag_label = tag.get("label", "") if isinstance(tag, dict) else str(tag)
                    if tag_label in self.ALLOWED_TAGS:
                        category = tag_label
                        break
                
                options = self._extract_options(markets_list)
                if not options:
                    return None
                
                options.sort(key=lambda x: x.price, reverse=True)
                lead_outcome_name = options[0].outcome
                
                one_day_change = 0.0
                one_hour_change = 0.0
                for market in markets_list:
                    group_title = market.get("groupItemTitle", "")
                    outcomes_raw = market.get("outcomes", [])
                    outcomes = eval(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
                    first_outcome = outcomes[0] if outcomes else ""
                    
                    is_match = (group_title == lead_outcome_name or 
                               first_outcome == lead_outcome_name or
                               (first_outcome in ["Yes", "No"] and group_title == lead_outcome_name))
                    
                    if is_match or len(markets_list) == 1:
                        one_day_change = float(market.get("oneDayPriceChange", 0) or 0)
                        one_hour_change = float(market.get("oneHourPriceChange", 0) or 0)
                        break
                
                return Market(
                    id=slug,
                    platform="polymarket",
                    question=e.get("title", "Unknown"),
                    volume=total_volume,
                    liquidity=total_liquidity,
                    end_date=datetime.fromisoformat(e.get("endDate", "2099-01-01T00:00:00Z").replace("Z", "+00:00")),
                    url=f"https://polymarket.com/event/{slug}",
                    last_price=options[0].price,
                    category=category,
                    lead_outcome=lead_outcome_name,
                    second_outcome=options[1].outcome if len(options) > 1 else "No",
                    second_price=options[1].price if len(options) > 1 else 0.0,
                    one_day_change=one_day_change,
                    one_hour_change=one_hour_change,
                    last_updated=datetime.now()
                )
        except Exception:
            return None
    
    async def stream_whale_trades(self, min_amount: float = 100000, interval: int = 15, max_age_minutes: int = 5):
        seen_trades = {}
        
        while True:
            now = datetime.now()
            cutoff = now - timedelta(minutes=max_age_minutes)
            
            seen_trades = {k: v for k, v in seen_trades.items() if v > cutoff}
            
            trades_data = await self.get_all_whale_trades(min_amount, limit=200)
            
            for item in trades_data:
                trade_id = item.get("transactionHash") or item.get("id")
                
                try:
                    trade_time = datetime.utcfromtimestamp(int(item.get("timestamp", 0)))
                except Exception:
                    continue
                
                if trade_time < cutoff:
                    continue
                
                if trade_id and trade_id not in seen_trades:
                    seen_trades[trade_id] = trade_time
                    event_slug = item.get("eventSlug", item.get("slug", "unknown"))
                    username = item.get("name", "")
                    pseudonym = item.get("pseudonym", "")
                    
                    size = float(item.get("size", 0))
                    price = float(item.get("price", 0))
                    
                    yield Trade(
                        id=trade_id,
                        platform="polymarket",
                        market_id=event_slug,
                        user_address=item.get("proxyWallet", "unknown"),
                        amount=size * price,
                        outcome=item.get("outcome", "unknown"),
                        price=price,
                        timestamp=trade_time,
                        url=f"https://polymarket.com/event/{event_slug}",
                        side=item.get("side"),
                        display_name=pseudonym or username or None,
                        profile_name=username if username and not username.startswith("0x") else None,
                    )
                    
            await asyncio.sleep(interval)
