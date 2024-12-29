# app/services/market_resolution.py
from typing import Any, List, Tuple, Optional, Dict
import functools
import time
from web3 import Web3, exceptions
from eth_utils import to_bytes
from eth_typing import HexAddress
from sqlalchemy.exc import SQLAlchemyError
from ..config import logger
from ..services.market_service import MarketService

def log_execution_time(func):
    """Decorator to measure and log execution time of market processing functions"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {str(e)}", exc_info=True)
            raise
    return wrapper

class MarketResolutionService:
    """
    Service to handle Polymarket resolution, including on-chain verification
    and position redemption for resolved markets.
    """
    def __init__(self, web3_service, postgres_service):
        self.w3 = web3_service.w3
        self.web3_service = web3_service
        self.ctf_contract = web3_service.ctf
        self.db = postgres_service
        self.market_service = MarketService()

    def _convert_condition_id_to_bytes32(self, condition_id: str) -> bytes:
        """
        Convert string condition ID to bytes32 format required by smart contract
        Args:
            condition_id: Market condition ID (hex or decimal string)
        Returns:
            bytes: 32-byte representation for contract interaction
        """
        try:
            if condition_id.startswith('0x'):
                return to_bytes(hexstr=condition_id)
            
            condition_int = int(condition_id)
            padded_hex = '0x' + hex(condition_int)[2:].zfill(64)
            return to_bytes(hexstr=padded_hex)
        except Exception as e:
            logger.error(f"Failed to convert condition ID {condition_id}: {str(e)}")
            raise

    async def _check_gamma_resolution(self, token_id: str) -> Optional[Tuple[bool, int]]:
        """
        Check market resolution via Gamma API
        Args:
            token_id: Market token ID
        Returns:
            Optional[Tuple[bool, int]]: (is_resolved, winning_outcome) or None if unresolved
        """
        try:
            market_data = await self.market_service.get_market(token_id)
            if not market_data:
                return None
                
            prices_str = market_data['outcome_prices'].strip('[]').split(',')
            prices = [float(p.strip()) for p in prices_str]
            
            if len(prices) != 2:
                logger.warning(f"Unexpected price format for token {token_id}: {prices}")
                return None
                
            # Map prices to outcomes: [1.0, 0.0] = YES won, [0.0, 1.0] = NO won
            if prices[0] == 1.0 and prices[1] == 0.0:
                return True, 1  # YES won
            elif prices[0] == 0.0 and prices[1] == 1.0:
                return True, 0  # NO won
            
            return False, None
            
        except ValueError as e:
            logger.info(f"Market data not available for token {token_id}")
            return None
        except Exception as e:
            logger.error(f"Error checking Gamma resolution for token {token_id}: {str(e)}")
            return None

    async def check_market_resolution(self, condition_id: str, token_id: str = None) -> Tuple[bool, Optional[int]]:
        """
        Enhanced market resolution check with multiple fallback mechanisms
        
        Args:
            condition_id: Market condition ID
            token_id: Token ID for Gamma lookup
        Returns:
            Tuple[bool, Optional[int]]: (is_resolved, winning_outcome)
        """
        try:
            # 1. First try on-chain verification
            try:
                condition_bytes = self._convert_condition_id_to_bytes32(condition_id)
                denominator = self.ctf_contract.functions.payoutDenominator(condition_bytes).call()
                
                if denominator > 0:
                    payout_numerators = self.ctf_contract.functions.payoutNumerators(condition_bytes).call()
                    winning_outcome = 1 if payout_numerators[1] > 0 else 0
                    return True, winning_outcome
            except exceptions.ContractLogicError as e:
                logger.info(f"Contract check failed for {condition_id}, trying Gamma: {str(e)}")

            # 2. Try Gamma API if we have token_id
            if token_id:
                try:
                    gamma_result = await self._check_gamma_resolution(token_id)
                    if gamma_result:
                        is_resolved, outcome = gamma_result
                        if is_resolved:
                            logger.info(f"Market {condition_id} resolved via Gamma with outcome: {outcome}")
                            return True, outcome
                except Exception as e:
                    logger.info(f"Gamma check failed for {token_id}, trying metadata: {str(e)}")

            # 3. Final fallback - check market metadata in database
            try:
                market = self.db.get_market(condition_id)
                if market and market.get('market_metadata'):
                    metadata = market['market_metadata']
                    outcome_prices = metadata.get('outcome_prices')
                    
                    if isinstance(outcome_prices, str):
                        import ast
                        outcome_prices = ast.literal_eval(outcome_prices)
                    
                    if outcome_prices == [1.0, 0.0]:
                        return True, 1
                    elif outcome_prices == [0.0, 1.0]:
                        return True, 0
            except Exception as e:
                logger.error(f"Metadata check failed for {condition_id}: {str(e)}")

            return False, None

        except Exception as e:
            logger.error(f"Error checking resolution status for {condition_id}: {str(e)}")
            return False, None

    def redeem_positions(
        self,
        user_address: str,
        condition_id: str,
        collateral_token: str,
        winning_outcome: int,
        amount: int
    ) -> Dict:
        """
        Redeem winning positions and transfer proceeds
        Args:
            user_address: Winner's address
            condition_id: Market condition ID
            collateral_token: USDC contract address
            winning_outcome: 0 for NO, 1 for YES
            amount: Position amount to redeem
        Returns:
            Dict with transaction details
        """
        try:
            condition_bytes = self._convert_condition_id_to_bytes32(condition_id)
            index_sets = [1] if winning_outcome == 0 else [2]
            
            tx = self.ctf_contract.functions.redeemPositions(
                Web3.to_checksum_address(collateral_token),
                '0x' + '0' * 64,  # parentCollectionId is always 0 for Polymarket
                condition_bytes,
                index_sets
            ).build_transaction({
                'from': self.w3.eth.default_account,
                'gas': 300000,
                'nonce': self.w3.eth.get_transaction_count(self.w3.eth.default_account)
            })
            
            signed_tx = self.w3.eth.account.sign_transaction(tx, self.w3.eth.account.key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)

            if receipt.status != 1:
                raise Exception("Redemption transaction failed")

            # Transfer USDC after successful redemption
            transfer_result = self.web3_service.transfer_usdc(
                Web3.to_checksum_address(user_address),
                amount
            )

            return {
                'success': True,
                'redemption_tx': receipt.transactionHash.hex(),
                'redemption_gas_used': receipt.gasUsed,
                'transfer_tx': transfer_result.get('transaction_hash'),
                'amount_transferred': amount
            }

        except Exception as e:
            logger.error(f"Failed to redeem and transfer for {condition_id}, user {user_address}: {str(e)}")
            raise

    def get_pending_redemptions(self) -> List[Dict[str, Any]]:
        """
        Fetch resolved markets that still need position processing.
        Returns markets that are resolved but not marked as processed.
        """
        query = """
            SELECT 
                condition_id,
                token_id,
                status,
                winning_outcome,
                market_metadata,
                created_at,
                resolved_at
            FROM markets 
            WHERE status = 'resolved'
            AND (processed_at IS NULL OR winning_outcome IS NOT NULL)
            ORDER BY resolved_at ASC
        """
        
        try:
            return self.execute_query(query)
        except SQLAlchemyError as e:
            logger.error("Failed to fetch pending redemptions", exc_info=True)
            raise

    @log_execution_time
    async def process_unresolved_markets(self) -> None:
        """
        Enhanced market processing that handles both resolution and redemption.
        """
        try:
            logger.info("=" * 50)
            logger.info("Starting market resolution process")
            logger.info("=" * 50)
            
            # First, handle unresolved markets
            unresolved_markets = self.db.get_unresolved_markets()
            if unresolved_markets:
                for market in unresolved_markets:
                    await self._process_market_resolution(market)
            
            # Then, handle pending redemptions
            pending_redemptions = self.db.get_pending_redemptions()
            if pending_redemptions:
                for market in pending_redemptions:
                    await self._process_market_redemptions(market)
                    
        except Exception as e:
            logger.error(f"Error in process_unresolved_markets: {str(e)}")
            raise

    async def _process_market_redemptions(self, market: Dict[str, Any]) -> None:
        """
        Process all winning positions for a resolved market.
        
        Args:
            market: Dictionary containing resolved market data
        """
        try:
            condition_id = market['condition_id']
            winning_outcome = market.get('winning_outcome')
            
            if winning_outcome is None:
                logger.error(f"Market {condition_id} has no winning outcome set")
                return
                
            
            # Get all winning positions
            positions = self.db.get_market_positions(condition_id)
            winning_positions = [p for p in positions if int(p['outcome']) == winning_outcome]
            
            if not winning_positions:
                self._mark_market_processed(condition_id)
                return
                
            
            for position in winning_positions:
                try:
                    user_address = position['user_address']
                    amount = position.get('amount', 0)
                    
                    if not amount:
                        logger.warning(f"No amount found for position: {position}")
                        continue

                    # Execute redemption and transfer
                    result = self.redeem_positions(
                        user_address=user_address,
                        condition_id=condition_id,
                        collateral_token=position['collateral_token'],
                        winning_outcome=winning_outcome,
                        amount=int(float(amount))
                    )
                    
                    logger.info(f"Redemption successful for user {user_address}: {result}")
                    
                    # Update position status
                    self.db.mark_position_redeemed(
                        condition_id,
                        user_address,
                        {
                            'redemption_tx': result['redemption_tx'],
                            'transfer_tx': result['transfer_tx'],
                            'amount_transferred': result['amount_transferred']
                        }
                    )
                    
                except Exception as e:
                    logger.error(f"Failed to process position for user {user_address}: {str(e)}")
                    continue
            
            # Mark market as fully processed
            self._mark_market_processed(condition_id)
            logger.info(f"Successfully processed all redemptions for market {condition_id}")
            
        except Exception as e:
            logger.error(f"Failed to process redemptions for market {condition_id}: {str(e)}")
            raise

    def _mark_market_processed(self, condition_id: str) -> None:
        """
        Mark a market as fully processed after handling all redemptions.
        """
        query = """
            UPDATE markets 
            SET processed_at = CURRENT_TIMESTAMP
            WHERE condition_id = :condition_id
        """
        
        try:
            self.db.execute_query(query, {"condition_id": condition_id})
        except SQLAlchemyError as e:
            logger.error(f"Failed to mark market {condition_id} as processed: {str(e)}")
            raise