import asyncio
from typing import Optional
from web3 import Web3
from decimal import Decimal
from ..models.db import Position
from ..config import logger
from .web3_service import Web3Service
from .postgres_service import PostgresService
from .clob_service import CLOBService
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

class PositionVerificationService:
    def __init__(self):
        self.web3_service = Web3Service()
        self.postgres_service = PostgresService()
        self.clob_service = CLOBService()

    async def verify_position_ownership(self, token_id: str, user_address: str, tokens_to_sell: float) -> Position:
        """Verify position ownership using CLOB client for balance checks"""
        try:
            # Step 1: Verify database position
            position = await self._verify_database_position(token_id, user_address, tokens_to_sell)
            
            # Step 2: Verify balance using CLOB client
            await self._verify_clob_balance(token_id, user_address, tokens_to_sell)
            
            return position
            
        except Exception as e:
            logger.error(f"Position verification failed: {str(e)}")
            raise ValueError(f"Could not verify position ownership: {str(e)}")

    async def _verify_database_position(self, token_id: str, user_address: str, tokens_to_sell: float) -> Position:
        """Verify position exists in database with correct amount"""
        db = self.postgres_service.get_db()
        try:
            position = db.query(Position).filter(
                Position.token_id == token_id,
                Position.user_address == user_address,
                Position.status == 'active'
            ).first()
            
            if not position:
                raise ValueError("No active position found for liquidation")
                
            position_decimal = float(position.amount)
            logger.info(f"Found position: {position_decimal} tokens at average entry {position.average_entry_price}")
            
            if abs(position_decimal - tokens_to_sell) > 0.0001:
                raise ValueError(f"Position amount mismatch. DB: {position_decimal}, Request: {tokens_to_sell}")
                
            return position
        finally:
            db.close()

    async def _verify_clob_balance(self, token_id: str, user_address: str, tokens_to_sell: float):
        """Verify balance using CLOB client with proper decimal handling"""
        try:
            logger.info(f"""
            Checking CLOB balance:
            Token ID: {token_id}
            User Address: {user_address}
            Required Amount: {tokens_to_sell}
            """)
            
            # Get balance with potential automatic update
            balance_info = await self.clob_service.get_balance(token_id)
            
            if not balance_info or 'decimal_balance' not in balance_info:
                raise ValueError("Failed to retrieve valid balance information")
                
            available_balance = float(balance_info['decimal_balance'])
            
            logger.info(f"""
            Balance Check Results:
            Raw Balance: {balance_info['balance']}
            Available Balance: {available_balance}
            Required Amount: {tokens_to_sell}
            Difference: {available_balance - tokens_to_sell}
            """)
            
            # Use a small epsilon for float comparison to handle rounding
            if available_balance < (tokens_to_sell - 0.0001):
                raise ValueError(
                    f"Insufficient balance. Available: {available_balance}, "
                    f"Required: {tokens_to_sell}"
                )
                
            return True
            
        except Exception as e:
            logger.error(f"CLOB balance verification failed: {str(e)}")
            raise ValueError(f"Failed to verify CLOB balance: {str(e)}")