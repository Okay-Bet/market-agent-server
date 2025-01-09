# src/services/sell_service.py
import asyncio
from decimal import Decimal
import time
from web3 import Web3
from py_clob_client.clob_types import OrderArgs, OrderType, BalanceAllowanceParams, AssetType, MarketOrderArgs
from py_clob_client.order_builder.constants import SELL
from ..config import logger, PRIVATE_KEY
from .web3_service import Web3Service
from .across_service import AcrossService
from .position_verification_service import PositionVerificationService
from .postgres_service import PostgresService 

class SellService:
    def __init__(self, trader_service):
        self.trader_service = trader_service
        self.web3_service = Web3Service()
        self.across_service = AcrossService()
        self.position_verification = PositionVerificationService()
        self.postgres_service = PostgresService()
        self.TOKEN_DECIMALS = 1_000_000
        self.USDC_DECIMALS = 1_000_000


    async def _handle_proceeds(self, user_address: str, amount: int) -> dict:
        """
        Handle the proceeds from a sale, including swap and bridge operations
        
        Args:
            user_address: User's address on Optimism (destination chain)
            amount: Amount of USDC.e in base units (6 decimals)
            
        Returns:
            dict: Transaction details including swap and bridge info
        """
        try:
            logger.info(f"Processing {amount/1_000_000} USDC.e proceeds for {user_address}")
            
            # Step 1: Execute swap from USDC.e to USDC
            try:
                swap_result = await self.web3_service.execute_swap(
                    amount=amount,
                    slippage_percent=0.5
                )
                
                if not swap_result["success"]:
                    raise ValueError(f"Swap failed: {swap_result.get('error')}")
                    
                logger.info(f"""
                Swap completed:
                Input: {amount/1_000_000} USDC.e
                Output: {swap_result['amounts']['output']['actual']['usdc']} USDC
                Route: {swap_result['route_used']['path']}
                """)
                
                # Get the actual USDC amount received
                usdc_amount = swap_result['amounts']['output']['actual']['base_units']
                
            except Exception as swap_error:
                logger.error(f"Swap operation failed: {str(swap_error)}")
                raise ValueError(f"Failed to swap USDC.e to USDC: {str(swap_error)}")
            
            # Step 2: Bridge USDC to Optimism using AcrossService
            try:
                # First get a quote to validate the bridge parameters
                quote = await self.across_service.get_bridge_quote(usdc_amount)
                
                # Initiate the bridge transfer
                bridge_result = await self.across_service.initiate_bridge(
                    user_address=user_address,
                    amount=usdc_amount
                )
                
                if not bridge_result["success"]:
                    raise ValueError(f"Bridge failed: {bridge_result.get('error')}")
                    
                logger.info(f"""
                Bridge initiated:
                Amount: {usdc_amount/1_000_000} USDC
                Recipient: {user_address}
                Estimated time: {bridge_result['bridge_details']['estimated_time']} seconds
                Transaction hash: {bridge_result['transaction_hash']}
                """)
                
            except Exception as bridge_error:
                logger.error(f"Bridge operation failed: {str(bridge_error)}")
                raise ValueError(f"Failed to bridge USDC to Optimism: {str(bridge_error)}")
            
            return {
                "success": True,
                "swap": {
                    "input_amount": amount,
                    "output_amount": usdc_amount,
                    "transaction_hash": swap_result["transaction_hash"]
                },
                "bridge": {
                    "input_amount": usdc_amount,
                    "output_amount": bridge_result["bridge_details"]["output_amount"],
                    "transaction_hash": bridge_result["transaction_hash"],
                    "estimated_time": bridge_result["bridge_details"]["estimated_time"]
                }
            }
            
        except Exception as e:
            logger.error(f"Proceeds handling failed: {str(e)}")
            raise ValueError(f"Failed to process proceeds: {str(e)}")

    async def execute_delegated_sell(self, token_id: str, price: float, amount: int, is_yes_token: bool, user_address: str):
        """
        Execute a delegated sell order, swap USDC.e to USDC, and bridge to user's Optimism wallet
        
        Args:
            token_id: The market token ID
            price: The selling price
            amount: Amount in USDC base units
            is_yes_token: Whether this is a YES token
            user_address: User's address on Optimism (destination chain)
        """
        try:
            # Authentication validation
            if not hasattr(self.trader_service.client, 'creds') or not self.trader_service.client.creds:
                logger.error("Client credentials not properly initialized")
                raise ValueError("Authentication not properly initialized")

            # Verify Level 2 auth before proceeding
            try:
                self.trader_service.client.assert_level_2_auth()
            except Exception as auth_error:
                logger.error(f"Level 2 authentication failed: {str(auth_error)}")
                # Re-initialize credentials
                self.trader_service.credentials = self.trader_service.client.create_or_derive_api_creds()
                self.trader_service.client.set_api_creds(self.trader_service.credentials)
                # Verify again
                self.trader_service.client.assert_level_2_auth()

            # Validate user address
            if not Web3.is_address(user_address):
                raise ValueError("Invalid user address")
            user_address = Web3.to_checksum_address(user_address)

            # Calculate amounts
            usdc_decimal = amount / self.USDC_DECIMALS
            tokens_to_sell = usdc_decimal / price
            tokens_to_sell_base = int(tokens_to_sell * self.TOKEN_DECIMALS)

            # [Previous position verification and approval code remains the same...]

            # Execute order with retries
            MAX_RETRIES = 3
            last_error = None
            
            for attempt in range(MAX_RETRIES):
                try:
                    # [Previous balance check and order execution code remains the same until the proceeds handling...]

                    # After successful order execution
                    actual_usdc_received = float(response.get('takingAmount', 0))
                    if actual_usdc_received <= 0:
                        raise ValueError("Invalid USDC amount received from trade")
                    
                    logger.info(f"""
                    Trade execution details:
                    Expected USDC: {usdc_decimal}
                    Actually received: {actual_usdc_received}
                    Difference: {actual_usdc_received - usdc_decimal}
                    """)
                    
                    # Close position in database
                    try:
                        self.postgres_service.close_position({
                            'token_id': token_id,
                            'user_address': user_address,
                            'exit_price': float(best_bid),
                            'amount': tokens_to_sell,
                            'transaction_hash': response.get('transactionHash', response.get('orderID'))
                        })
                        logger.info(f"Successfully closed position in database for token {token_id}")
                    except Exception as db_error:
                        logger.error(f"Failed to close position in database: {str(db_error)}")

                    # Handle proceeds (swap and bridge)
                    usdc_e_amount = int(actual_usdc_received * self.USDC_DECIMALS)
                    proceeds_result = await self._handle_proceeds(user_address, usdc_e_amount)
                    
                    return {
                        "success": True,
                        "order_id": response.get("orderID"),
                        "status": response.get("status"),
                        "details": {
                            "tokens_sold": tokens_to_sell,
                            "expected_usdc": usdc_decimal,
                            "actual_usdc": actual_usdc_received,
                            "price": price,
                            "best_bid": best_bid,
                            "transaction_hashes": response.get("transactionsHashes", [])
                        },
                        "proceeds": proceeds_result,  # New field containing swap and bridge details
                        "position_id": str(position.id)
                    }

                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"Attempt {attempt + 1} failed: {last_error}")
                    
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(3)
                        continue
                    raise ValueError(f"Failed after {MAX_RETRIES} attempts. Last error: {last_error}")

        except Exception as e:
            logger.error(f"Delegated sell execution failed: {str(e)}")
            raise ValueError(str(e))