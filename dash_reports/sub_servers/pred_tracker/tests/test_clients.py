import asyncio

from dash_reports.sub_servers.pred_tracker.kalshi.api.async_client import AsyncKalshiClient
from dash_reports.sub_servers.pred_tracker.poly.api.async_events_client import AsyncPolymarketEventsClient


async def _fetch_markets():
    async with AsyncKalshiClient(min_liquidity=1000) as kalshi:
        markets = await kalshi.get_markets()
        assert isinstance(markets, list)
        assert all(market.question for market in markets)
    async with AsyncPolymarketEventsClient(min_volume=10000) as poly:
        markets = await poly.get_events()
        assert isinstance(markets, list)
        assert all(market.question for market in markets)


def test_both():
    asyncio.run(_fetch_markets())
