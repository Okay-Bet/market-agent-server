# src/services/market_service.py
import httpx
from ..config import GAMMA_MARKETS_ENDPOINT, logger

class MarketService:
    @staticmethod
    async def get_market(token_id: str) -> dict:
        params = {"clob_token_ids": token_id}
        async with httpx.AsyncClient() as client:
            res = await client.get(GAMMA_MARKETS_ENDPOINT, params=params)
            if res.status_code == 200:
                data = res.json()
                if data:
                    market = data[0]
                    return {
                        "id": int(market["id"]),
                        "question": market["question"],
                        "outcomes": str(market["outcomes"]),
                        "outcome_prices": str(market["outcomePrices"]),
                    }
            raise ValueError(f"Could not fetch market data for token {token_id}")