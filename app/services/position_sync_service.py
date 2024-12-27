from typing import List
from dataclasses import asdict
from ..models.db import Market
from ..config import logger
from ..services.trader_service import Position

class PositionSyncService:
    def __init__(self, postgres_service):
        self.db = postgres_service

    async def sync_position_markets(self, positions: List[Position]) -> None:
        """
        Ensures all markets from positions exist in the database.
        
        Args:
            positions: List of Position objects from trader service
        """
        try:
            for position in positions:
                # Convert position to dict for easier handling
                market_data = {
                    'condition_id': position.token_id,  # Using token_id as condition_id
                    'metadata': {
                        'market_id': position.market_id,
                        'question': position.market_question,
                        'outcomes': position.outcomes,
                        'outcome_prices': position.prices
                    }
                }
                
                # Check if market exists
                existing_market = self.db.get_market(position.token_id)
                
                if not existing_market:
                    logger.info(f"Creating new market entry for token_id: {position.token_id}")
                    self.db.create_market(market_data)
                elif existing_market['status'] != 'resolved':
                    # Update market metadata if it exists but isn't resolved
                    logger.info(f"Updating market metadata for token_id: {position.token_id}")
                    self.db.update_market_metadata(position.token_id, market_data['metadata'])
                
        except Exception as e:
            logger.error(f"Error syncing position markets: {str(e)}")
            raise