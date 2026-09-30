import aiohttp
import asyncio
import logging
import ssl
import certifi
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from collections import defaultdict
from dash_reports.sub_servers.pred_tracker.models import Market, Trade, MarketOption


class AsyncKalshiClient:
    BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
    FALLBACK_URL = "https://trading-api.kalshi.com/trade-api/v2"
    
    ALLOWED_CATEGORIES = {
        "Politics", "Economics", "Financials", "World",
        "Crypto", "Companies", "Earnings"
    }
    
    FINANCE_CATEGORIES = {"Companies", "Financials", "Economics"}
    
    def __init__(self, min_liquidity: float = 1000000):
        self.session: Optional[aiohttp.ClientSession] = None
        self.min_liquidity = min_liquidity
        self.allowed_series = None
        self._series_to_category = {}
        self._ssl_context = self._create_ssl_context()
        
    def _create_ssl_context(self):
        try:
            return ssl.create_default_context(cafile=certifi.where())
        except Exception as e:
            logging.warning(f"SSL context fallback: {e}")
            return ssl.create_default_context()
    
    @staticmethod
    def _calc_tob_liquidity(m: dict) -> float:
        bid_size = float(m.get("yes_bid_size_fp") or 0)
        ask_size = float(m.get("yes_ask_size_fp") or 0)
        bid_price = float(m.get("yes_bid_dollars") or 0)
        ask_price = float(m.get("yes_ask_dollars") or 0)
        return (bid_size * bid_price) + (ask_size * (1 - ask_price))
        
    async def __aenter__(self):
        connector = aiohttp.TCPConnector(ssl=self._ssl_context)
        self.session = aiohttp.ClientSession(connector=connector)
        await self._load_allowed_series()
        return self
        
    async def _load_allowed_series(self):
        urls_to_try = [self.BASE_URL, self.FALLBACK_URL]
        
        for base_url in urls_to_try:
            try:
                async with self.session.get(f"{base_url}/series", timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self.allowed_series = set()
                        
                        for series in data.get('series', []):
                            category = series.get('category', '')
                            ticker = series.get('ticker')
                            
                            if category in self.ALLOWED_CATEGORIES:
                                self.allowed_series.add(ticker)
                            self._series_to_category[ticker] = category
                        
                        if self.allowed_series:
                            self.BASE_URL = base_url
                            logging.info(f"Kalshi: Loaded {len(self.allowed_series)} allowed series from {base_url}")
                            return
                        else:
                            logging.warning(f"Kalshi: No allowed series found at {base_url}")
                    else:
                        logging.warning(f"Kalshi series API returned {resp.status} from {base_url}")
            except Exception as e:
                logging.warning(f"Kalshi series load failed from {base_url}: {e}")
        
        logging.error("Kalshi: All API endpoints failed - no series loaded")
        self.allowed_series = set()
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    def _get_category(self, series: str) -> str:
        return self._series_to_category.get(series, "Unknown")
    
    def _is_multi_option_event(self, markets: List[Dict]) -> bool:
        if len(markets) <= 1:
            return False
        
        titles = set(m.get("title", "") for m in markets)
        if len(titles) != 1:
            return False
        
        yes_subs = [m.get("yes_sub_title", "") for m in markets]
        unique_yes_subs = set(yes_subs)
        
        if len(unique_yes_subs) >= 2 and all(yes_subs):
            return True
        
        sample = markets[0]
        yes_sub = sample.get("yes_sub_title", "")
        no_sub = sample.get("no_sub_title", "")
        
        if yes_sub and no_sub and yes_sub == no_sub:
            if any(k in yes_sub for k in ["Before", "After", "By"]):
                return all(
                    m.get("yes_sub_title", "") == m.get("no_sub_title", "")
                    for m in markets)
        
        return False
            
    async def get_markets(self, limit: int = 1000, progress_callback=None) -> List[Market]:
        if not self.allowed_series:
            logging.warning("Kalshi: No allowed series loaded - attempting reload")
            await self._load_allowed_series()
            if not self.allowed_series:
                logging.error("Kalshi: Still no series after reload - returning empty")
                return []
        
        raw_by_event = defaultdict(list)
        cursor = None
        page = 0
        
        while True:
            page += 1
            params = {"limit": 1000, "status": "open", "mve_filter": "exclude"}
            if cursor:
                params["cursor"] = cursor
            
            try:
                async with self.session.get(f"{self.BASE_URL}/markets", params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 429:
                        logging.warning(f"Kalshi: Rate limit hit on page {page}")
                        break
                    if resp.status != 200:
                        logging.error(f"Kalshi: Markets API returned {resp.status} on page {page}")
                        break
                    data = await resp.json()
            except asyncio.TimeoutError:
                logging.error(f"Kalshi: Timeout on page {page}")
                break
            except Exception as e:
                logging.error(f"Kalshi: API error on page {page}: {e}")
                break
            
            page_markets = data.get("markets", [])
            cursor = data.get("cursor")
            
            await asyncio.sleep(0.2)
            
            for item in page_markets:
                ticker = item.get("ticker", "")
                if not ticker:
                    continue
                series = ticker.split('-')[0]
                if series not in self.allowed_series:
                    continue
                event_ticker = item.get("event_ticker", ticker)
                raw_by_event[event_ticker].append(item)
            
            if not cursor:
                break
        
        logging.info(f"Kalshi: Collected {sum(len(v) for v in raw_by_event.values())} raw markets across {len(raw_by_event)} events")
        
        seen = set()
        final_markets = []
        
        for event_ticker, event_markets in raw_by_event.items():
            if self._is_multi_option_event(event_markets):
                market = self._create_multi_option(event_markets, event_ticker, seen)
            else:
                market = self._create_binary_markets(event_markets, seen)
            
            if isinstance(market, list):
                final_markets.extend(market)
            elif market:
                final_markets.append(market)
        
        multi_count = sum(1 for m in final_markets if m.is_multi_option)
        logging.info(f"Kalshi: Created {len(final_markets)} markets ({multi_count} multi-option)")
        
        
        if not final_markets and raw_by_event:
            sample_event = next(iter(raw_by_event.values()))[:3]
            for m in sample_event:
                vol_fields = {k: v for k, v in m.items() if 'vol' in k.lower() or 'liq' in k.lower() or 'dollar' in k.lower() or 'amount' in k.lower()}
                logging.warning(f"Kalshi debug: ticker={m.get('ticker')}, category={self._get_category(m.get('ticker','').split('-')[0])}")
                logging.warning(f"Kalshi debug fields: volume={m.get('volume')}, liquidity={m.get('liquidity')}, related={vol_fields}")
                logging.warning(f"Kalshi debug all keys: {list(m.keys())[:20]}")
        
        if progress_callback:
            await progress_callback("kalshi", final_markets, 1, True)
        
        return final_markets
    
    def _create_multi_option(self, markets: List[Dict], event_ticker: str, seen: set) -> Optional[Market]:
        if event_ticker in seen:
            return None
        seen.add(event_ticker)
        
        sorted_markets = sorted(markets, key=lambda m: m.get("last_price", 0), reverse=True)
        lead = sorted_markets[0]
        
        ticker = lead.get("ticker", "")
        series = ticker.split('-')[0]
        category = self._get_category(series)
        
        total_volume = sum(float(m.get("volume") or m.get("volume_fp") or 0) for m in markets)
        total_liquidity = sum(self._calc_tob_liquidity(m) for m in markets)
        total_oi = sum(float(m.get("dollar_open_interest") or m.get("open_interest_fp") or m.get("open_interest") or 0) for m in markets)
        
        min_vol = 10_000 if category in self.FINANCE_CATEGORIES else 100_000
        if total_volume < min_vol:
            return None
        
        options = []
        for m in sorted_markets:
            outcome = m.get("yes_sub_title", "Yes")
            price_raw = m.get("last_price_dollars") or m.get("last_price") or 0
            prev_raw = m.get("previous_price_dollars") or m.get("previous_price") or 0
            price = float(price_raw)
            prev = float(prev_raw)
            if price > 1:
                price /= 100.0
                prev /= 100.0
            options.append(MarketOption(outcome=outcome, price=price, change=price - prev))
        
        no_price = 1.0 - sum(o.price for o in options)
        no_prev = 1.0 - sum(o.price - o.change for o in options)
        if no_price > 0.01:
            options.insert(0, MarketOption(outcome="No", price=no_price, change=no_price - no_prev))
        
        options.sort(key=lambda x: x.price, reverse=True)
        
        close_time = lead.get("close_time", "") or lead.get("expiration_time", "") or "2025-12-31T00:00:00Z"
        try:
            end_date = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
        except:
            end_date = datetime(2025, 12, 31)
        
        series_ticker = event_ticker.split('-')[0] if event_ticker else ticker.split('-')[0]
        
        return Market(
            id=event_ticker,
            platform="kalshi",
            question=lead.get("title", "Unknown"),
            volume=total_volume,
            liquidity=total_liquidity,
            end_date=end_date,
            url=f"https://kalshi.com/markets/{series_ticker}",
            last_price=options[0].price,
            category=category,
            open_interest=total_oi,
            lead_outcome=options[0].outcome,
            second_outcome=options[1].outcome if len(options) > 1 else "Other",
            second_price=options[1].price if len(options) > 1 else 0.0,
            one_day_change=options[0].change,
            one_hour_change=0.0,
            last_updated=datetime.now(),
            is_multi_option=True,
            options=options,
            event_group=event_ticker
        )
    
    def _create_binary_markets(self, event_markets: List[Dict], seen: set) -> List[Market]:
        markets = []
        for item in event_markets:
            market = self._create_single_binary(item, seen)
            if market:
                markets.append(market)
        return markets
    
    def _create_single_binary(self, item: Dict, seen: set) -> Optional[Market]:
        ticker = item.get("ticker", "")
        series = ticker.split('-')[0]
        category = self._get_category(series)
        
        title = item.get("title", "Unknown")
        subtitle = item.get("subtitle", "")
        yes_sub = item.get("yes_sub_title", "")
        no_sub = item.get("no_sub_title", "")
        
        ticker_parts = ticker.split('-')
        is_time_series = len(ticker_parts) >= 3 and yes_sub and no_sub
        
        if is_time_series:
            question = title
            lead_outcome = yes_sub or "Yes"
            second_outcome = f"After {yes_sub.replace('Before ', '')}" if yes_sub.startswith('Before') else "No"
        else:
            question = f"{title}: {subtitle}" if subtitle and len(subtitle) >= 10 and not subtitle.replace(".", "").replace("%", "").replace("-", "").replace(">", "").replace("<", "").replace(" ", "").isdigit() else title
            lead_outcome = "Yes"
            second_outcome = "No"
        
        event_ticker = item.get("event_ticker", ticker)
        dedup_key = ticker if is_time_series else event_ticker
        
        if dedup_key in seen:
            return None
        seen.add(dedup_key)
        
        volume = float(item.get("volume") or item.get("volume_fp") or 0)
        liquidity = self._calc_tob_liquidity(item)
        oi = float(item.get("dollar_open_interest") or item.get("open_interest_fp") or item.get("open_interest") or 0)
        
        min_vol = 10_000 if category in self.FINANCE_CATEGORIES else 100_000
        if volume < min_vol:
            return None
        
        last_price_raw = item.get("last_price_dollars") or item.get("last_price")
        prev_price_raw = item.get("previous_price_dollars") or item.get("previous_price")
        last_price = float(last_price_raw) if last_price_raw else 0.5
        prev_price = float(prev_price_raw) if prev_price_raw else 0.5
        if last_price > 1:
            last_price /= 100.0
            prev_price /= 100.0
        no_price = 1.0 - last_price
        prev_no = 1.0 - prev_price
        
        if is_time_series:
            second_price = no_price
            change = last_price - prev_price
        elif last_price >= no_price:
            second_price = no_price
            change = last_price - prev_price
        else:
            second_price = last_price
            last_price = no_price
            lead_outcome = "No"
            second_outcome = "Yes"
            change = no_price - prev_no
        
        close_time = item.get("close_time", "") or item.get("expiration_time", "") or "2025-12-31T00:00:00Z"
        try:
            end_date = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
        except:
            end_date = datetime(2025, 12, 31)
        
        series_ticker = event_ticker.split('-')[0] if event_ticker else ticker.split('-')[0]
        
        return Market(
            id=ticker,
            platform="kalshi",
            question=question,
            volume=volume,
            liquidity=liquidity,
            end_date=end_date,
            url=f"https://kalshi.com/markets/{series_ticker}",
            last_price=last_price,
            category=category,
            open_interest=oi,
            lead_outcome=lead_outcome,
            second_outcome=second_outcome,
            second_price=second_price,
            one_day_change=change,
            one_hour_change=0.0,
            last_updated=datetime.now()
        )
    
    async def get_all_trades(self, limit: int = 1000, min_ts: int = None) -> list:
        params = {"limit": limit}
        if min_ts:
            params["min_ts"] = min_ts
        try:
            async with self.session.get(f"{self.BASE_URL}/markets/trades", params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data.get("trades", [])
        except Exception:
            return []
    
    async def get_single_market(self, ticker: str) -> Optional[Market]:
        try:
            async with self.session.get(f"{self.BASE_URL}/markets/{ticker}", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                resp.raise_for_status()
                data = await resp.json()
                m = data.get("market", {})
                
                event_ticker = m.get("event_ticker", "")
                series_ticker = event_ticker.rsplit("-", 1)[0].lower() if "-" in event_ticker else event_ticker.lower()
                category = self._series_to_category.get(m.get("series_ticker"), "")
                
                event_markets = await self._get_event_markets(event_ticker)
                
                if event_markets and self._is_multi_option_event(event_markets):
                    return self._build_multi_option_market(event_markets, event_ticker, series_ticker, category)
                
                return self._build_binary_market(m, ticker, series_ticker, category)
        except Exception:
            return None
    
    async def _get_event_markets(self, event_ticker: str) -> List[Dict]:
        try:
            async with self.session.get(f"{self.BASE_URL}/markets", params={"event_ticker": event_ticker}, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data.get("markets", [])
        except Exception:
            return []
    
    def _build_multi_option_market(self, markets: List[Dict], event_ticker: str, series_ticker: str, category: str) -> Market:
        sorted_markets = sorted(markets, key=lambda m: float(m.get("last_price_dollars") or m.get("last_price") or 0), reverse=True)
        lead = sorted_markets[0]
        
        total_volume = sum(float(m.get("volume") or m.get("volume_fp") or 0) for m in markets)
        total_liquidity = sum(AsyncKalshiClient._calc_tob_liquidity(m) for m in markets)
        total_oi = sum(float(m.get("dollar_open_interest") or m.get("open_interest_fp") or m.get("open_interest") or 0) for m in markets)
        
        options = []
        for m in sorted_markets:
            outcome = m.get("yes_sub_title", "Yes")
            price_raw = float(m.get("last_price_dollars") or m.get("last_price") or 0)
            prev_raw = float(m.get("previous_price_dollars") or m.get("previous_price") or 0)
            price = price_raw / 100.0 if price_raw > 1 else price_raw
            prev = prev_raw / 100.0 if prev_raw > 1 else prev_raw
            options.append(MarketOption(outcome=outcome, price=price, change=price - prev))
        
        no_price = 1.0 - sum(o.price for o in options)
        if no_price > 0.01:
            no_prev = 1.0 - sum(o.price - o.change for o in options)
            options.insert(0, MarketOption(outcome="None", price=no_price, change=no_price - no_prev))
        
        options.sort(key=lambda x: x.price, reverse=True)
        
        close_time = lead.get("close_time", "") or "2099-01-01T00:00:00Z"
        end_date = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
        
        return Market(
            id=event_ticker,
            platform="kalshi",
            question=lead.get("title", "Unknown"),
            volume=total_volume,
            liquidity=total_liquidity,
            end_date=end_date,
            url=f"https://kalshi.com/markets/{series_ticker}",
            last_price=options[0].price,
            category=category,
            open_interest=total_oi,
            lead_outcome=options[0].outcome,
            second_outcome=options[1].outcome if len(options) > 1 else "Other",
            second_price=options[1].price if len(options) > 1 else 0.0,
            one_day_change=options[0].change,
            last_updated=datetime.now(),
            is_multi_option=True,
            options=options
        )
    
    def _build_binary_market(self, m: Dict, ticker: str, series_ticker: str, category: str) -> Market:
        title = m.get("title", "Unknown")
        price_raw = float(m.get("last_price_dollars") or m.get("last_price") or 0)
        last_price = price_raw / 100.0 if price_raw > 1 else price_raw
        no_price = 1.0 - last_price
        
        yes_sub = m.get("yes_sub_title", "")
        if yes_sub and yes_sub == m.get("no_sub_title", ""):
            lead_outcome = yes_sub
            second_outcome = "No"
        else:
            lead_outcome = "Yes"
            second_outcome = "No"
        
        close_time = m.get("close_time", "") or "2099-01-01T00:00:00Z"
        end_date = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
        
        volume = float(m.get("volume") or m.get("volume_fp") or 0)
        liquidity = AsyncKalshiClient._calc_tob_liquidity(m)
        oi = float(m.get("dollar_open_interest") or m.get("open_interest_fp") or m.get("open_interest") or 0)

        return Market(
            id=ticker,
            platform="kalshi",
            question=title,
            volume=volume,
            liquidity=liquidity,
            end_date=end_date,
            url=f"https://kalshi.com/markets/{series_ticker}",
            last_price=last_price,
            category=category,
            open_interest=oi,
            lead_outcome=lead_outcome,
            second_outcome=second_outcome,
            second_price=no_price,
            last_updated=datetime.now()
        )
    
    async def stream_whale_trades(self, min_amount: float = 100000, interval: int = 15, max_age_minutes: int = 5):
        seen_trades = {}
        
        while True:
            now = datetime.now()
            cutoff = now - timedelta(minutes=max_age_minutes)
            
            seen_trades = {k: v for k, v in seen_trades.items() if v > cutoff}
            
            trades_data = await self.get_all_trades(limit=500)
            
            for item in trades_data:
                trade_id = item.get("trade_id")
                count = float(item.get("count", 0))
                yes_price = float(item.get("yes_price", 0))
                amount = count * yes_price / 100.0
                
                try:
                    trade_time = datetime.fromisoformat(item.get("created_time", "").replace("Z", "+00:00")).replace(tzinfo=None)
                except Exception:
                    continue
                
                if trade_time < cutoff:
                    continue
                
                if amount >= min_amount and trade_id and trade_id not in seen_trades:
                    seen_trades[trade_id] = trade_time
                    ticker = item.get("ticker", "unknown")
                    series = ticker.rsplit("-", 2)[0].lower() if "-" in ticker else ticker.lower()
                    yield Trade(
                        id=trade_id,
                        platform="kalshi",
                        market_id=ticker,
                        user_address="anonymous",
                        amount=amount,
                        outcome=item.get("taker_side", "unknown"),
                        price=yes_price / 100.0,
                        timestamp=trade_time,
                        url=f"https://kalshi.com/markets/{series}"
                    )
                    
            await asyncio.sleep(interval)
