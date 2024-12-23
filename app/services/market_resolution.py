from typing import Dict, List
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from eth_typing import HexAddress
from web3 import Web3
from ..services.web3_service import Web3Service
from ..services.postgres_service import PostgresService
from ..config import logger
import functools
import time

def log_execution_time(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        logger.info(f"Starting {func.__name__}")
        try:
            result = await func(*args, **kwargs)
            logger.info(f"Completed {func.__name__} in {time.time() - start_time:.2f} seconds")
            return result
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {str(e)}", exc_info=True)
            raise
    return wrapper


class MarketResolutionService:
    def __init__(self, web3_service: Web3Service, postgres_service: PostgresService):
        self.w3 = web3_service.w3
        self.web3_service = web3_service
        self.ctf_contract = web3_service.ctf
        self.db = postgres_service
        self.scheduler = AsyncIOScheduler()

    def start(self):
        """Start the resolution job scheduler"""
        if not self.scheduler.running:
            self.scheduler.add_job(
                self.process_unresolved_markets,
                'interval',
                minutes=5,
                id='market_resolution_job',
                replace_existing=True
            )
            self.scheduler.start()
            logger.info("Market resolution job scheduler started")
        else:
            logger.warning("Scheduler is already running")

    def stop(self):
        """Stop the resolution job scheduler"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Market resolution job scheduler stopped")
        else:
            logger.warning("Scheduler is not running")

    async def check_market_resolution(self, condition_id: str) -> bool:
        """Check if a market has been resolved by checking payoutDenominator"""
        try:
            payout_denominator = await self.ctf_contract.functions.payoutDenominator(condition_id).call()
            return payout_denominator > 0
        except Exception as e:
            logger.error(f"Failed to check market resolution for {condition_id}: {str(e)}")
            return False

    async def get_winning_outcome(self, condition_id: str) -> int:
        """Get winning outcome (0 for NO, 1 for YES)"""
        try:
            payout_numerators = await self.ctf_contract.functions.payoutNumerators(condition_id).call()
            return 1 if payout_numerators[1] > 0 else 0
        except Exception as e:
            logger.error(f"Failed to get winning outcome for {condition_id}: {str(e)}")
            raise

    async def redeem_and_transfer(
        self,
        user_address: HexAddress,
        condition_id: str,
        collateral_token: HexAddress,
        winning_outcome: int,
        amount: int
    ) -> Dict:
        """Redeem winning position tokens and transfer proceeds to user"""
        try:
            # First redeem the position
            index_sets = [1] if winning_outcome == 0 else [2]
            
            tx = await self.ctf_contract.functions.redeemPositions(
                collateral_token,
                "0x" + "0" * 64,  # parentCollectionId is always 0 for Polymarket
                condition_id,
                index_sets
            ).build_transaction({
                'from': self.w3.eth.default_account,
                'gas': 300000,
                'nonce': await self.w3.eth.get_transaction_count(self.w3.eth.default_account)
            })
            
            signed_tx = self.w3.eth.account.sign_transaction(tx, self.w3.eth.account.key)
            tx_hash = await self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            receipt = await self.w3.eth.wait_for_transaction_receipt(tx_hash)

            if receipt.status != 1:
                raise Exception("Redemption transaction failed")

            # After successful redemption, transfer USDC to user
            transfer_result = await self.web3_service.transfer_usdc(
                Web3.to_checksum_address(user_address),
                amount
            )

            if not transfer_result.get('success'):
                raise Exception(f"USDC transfer failed: {transfer_result.get('error')}")

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

    @log_execution_time
    async def process_unresolved_markets(self):
        """Process all unresolved markets"""
        try:
            logger.info("=" * 50)
            logger.info("Starting market resolution process")
            logger.info("=" * 50)
            
            # Get all unresolved markets from database
            unresolved_markets = await self.db.get_unresolved_markets()
            logger.info(f"Found {len(unresolved_markets) if unresolved_markets else 0} unresolved markets")
            
            if not unresolved_markets:
                logger.info("No unresolved markets to process")
                return
                
            logger.info(f"Market details: {unresolved_markets}")
            
            for market in unresolved_markets:
                condition_id = market['condition_id']
                logger.info("-" * 30)
                logger.info(f"Processing market {condition_id}")
                
                try:
                    # Check if market is resolved
                    is_resolved = await self.check_market_resolution(condition_id)
                    logger.info(f"Market {condition_id} resolution status: {is_resolved}")
                    
                    if not is_resolved:
                        logger.info(f"Market {condition_id} not yet resolved, skipping")
                        continue

                    # Get winning outcome
                    winning_outcome = await self.get_winning_outcome(condition_id)
                    logger.info(f"Market {condition_id} resolved with outcome: {'YES' if winning_outcome == 1 else 'NO'}")
                    
                    # Get all positions for this market
                    positions = await self.db.get_market_positions(condition_id)
                    logger.info(f"Found {len(positions)} positions for market {condition_id}")
                    
                    winning_positions = [p for p in positions if int(p['outcome']) == winning_outcome]
                    logger.info(f"Found {len(winning_positions)} winning positions to process")
                    
                    # Process winning positions
                    for position in winning_positions:
                        user_address = position['user_address']
                        logger.info(f"Processing winning position for user {user_address}")
                        
                        try:
                            amount = position.get('amount', 0)
                            logger.info(f"Position amount: {amount}")
                            
                            if not amount:
                                logger.warning(f"No amount found for position: {position}")
                                continue

                            # Redeem position and transfer USDC
                            logger.info(f"Attempting redemption for user {user_address}")
                            result = await self.redeem_and_transfer(
                                user_address=user_address,
                                condition_id=condition_id,
                                collateral_token=position['collateral_token'],
                                winning_outcome=winning_outcome,
                                amount=int(float(amount))  # Convert from decimal string to int
                            )
                            
                            logger.info(f"Redemption successful for user {user_address}: {result}")
                            
                            # Update position status in database
                            await self.db.mark_position_redeemed(
                                condition_id,
                                user_address,
                                {
                                    'redemption_tx': result['redemption_tx'],
                                    'transfer_tx': result['transfer_tx'],
                                    'amount_transferred': result['amount_transferred']
                                }
                            )
                            logger.info(f"Position marked as redeemed in database for user {user_address}")

                        except Exception as e:
                            logger.error(f"Failed to process position for user {user_address}: {str(e)}", exc_info=True)
                            continue
                    
                    # Mark market as resolved in database
                    current_block = await self.w3.eth.get_block('latest')
                    await self.db.mark_market_resolved(
                        condition_id,
                        winning_outcome,
                        {
                            "timestamp": current_block.timestamp,
                            "processed_at": current_block.timestamp
                        }
                    )
                    
                    logger.info(f"Successfully completed processing for market {condition_id}")
                    
                except Exception as e:
                    logger.error(f"Failed to process market {condition_id}: {str(e)}", exc_info=True)
                    continue

        except Exception as e:
            logger.error(f"Error in process_unresolved_markets: {str(e)}", exc_info=True)