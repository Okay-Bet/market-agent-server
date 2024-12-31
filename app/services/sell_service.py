# src/services/sell_service.py
import asyncio
from decimal import Decimal
import time
from web3 import Web3
from py_clob_client.clob_types import OrderArgs, OrderType, BalanceAllowanceParams, AssetType, MarketOrderArgs
from py_clob_client.order_builder.constants import SELL
from ..config import logger, PRIVATE_KEY
from .web3_service import Web3Service

class SellService:
    def __init__(self, trader_service):
        self.trader_service = trader_service
        self.web3_service = Web3Service()
        self.TOKEN_DECIMALS = 1_000_000
        self.USDC_DECIMALS = 1_000_000


    async def execute_delegated_sell(self, token_id: str, price: float, amount: int, is_yes_token: bool, user_address: str):
        """
        Execute a delegated sell order and transfer proceeds to user's wallet
        
        Args:
            token_id: The market token ID
            price: The selling price
            amount: Amount in USDC base units
            is_yes_token: Whether this is a YES token
            user_address: Address to receive the proceeds
        """
        try:

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

            # Validate user address for later transfer
            if not Web3.is_address(user_address):
                raise ValueError("Invalid user address")
            user_address = Web3.to_checksum_address(user_address)

            # Step 1: Check all contract approvals
            approvals = self.web3_service.check_all_approvals()
            logger.info(f"Current approval status: {approvals}")
            
            needs_approval = False
            for name, status in approvals.items():
                if not status["ctf_approved"] or status["usdc_allowance"] <= 0:
                    needs_approval = True
                    logger.info(f"Missing approvals for {name}")
                    break
            
            if needs_approval:
                logger.info("Some approvals missing, initiating approval process")
                approval_result = await self.web3_service.approve_all_contracts()
                if not approval_result["success"]:
                    raise ValueError(f"Failed to approve contracts: {approval_result.get('error')}")
                await asyncio.sleep(3)  # Wait for approvals to propagate
                
                # Verify approvals again
                approvals = self.web3_service.check_all_approvals()
                logger.info(f"Updated approval status: {approvals}")
                
                for name, status in approvals.items():
                    if not status["ctf_approved"] or status["usdc_allowance"] <= 0:
                        raise ValueError(f"Approval failed for {name} after attempt")

            # Step 2: Check orderbook for liquidity
            orderbook = self.trader_service.client.get_order_book(token_id)
            if not orderbook.bids:
                raise ValueError("No buy orders available in orderbook")

            # Sort bids by price (highest first)
            sorted_bids = sorted(
                [(float(b.price), float(b.size)) for b in orderbook.bids],
                key=lambda x: x[0],
                reverse=True
            )
            if not sorted_bids:
                raise ValueError("No valid bids after sorting")

            best_bid = sorted_bids[0][0]
            logger.info(f"Best bid after sorting: {best_bid}")

            if float(price) < best_bid * 0.99:  # 1% tolerance
                raise ValueError(f"Sell price ({price}) too low compared to best bid ({best_bid})")

            # Step 3: Calculate amounts and verify balance
             # Calculate actual token amount from USDC amount
            usdc_decimal = amount / self.USDC_DECIMALS  # Convert USDC to decimal
            tokens_to_sell = usdc_decimal / price       # Calculate number of tokens needed
            tokens_to_sell_base = int(tokens_to_sell * self.TOKEN_DECIMALS)  # Convert to base units for balance check

            # Minimum order size check (5 tokens)
            MIN_ORDER_SIZE = 5.0
            if tokens_to_sell < MIN_ORDER_SIZE:
                raise ValueError(f"Order size ({tokens_to_sell:.2f} tokens) is below minimum size of {MIN_ORDER_SIZE} tokens")


            # Update and verify balance allowance (using server's balance)
            MAX_RETRIES = 3
            last_error = None
            
            for attempt in range(MAX_RETRIES):
                try:
                    # Set up balance params for server account
                    balance_params = BalanceAllowanceParams(
                        asset_type=AssetType.CONDITIONAL,
                        token_id=token_id,
                        signature_type=0
                    )
                    
                    # Force balance allowance update
                    logger.info(f"Updating balance allowance (attempt {attempt + 1})")
                    updated_balance = self.trader_service.client.update_balance_allowance(balance_params)
                    await asyncio.sleep(2)
                    
                    # Verify server's balance
                    current_balance = self.trader_service.client.get_balance_allowance(balance_params)
                    logger.info(f"Current balance state: {current_balance}")
                    
                    if not current_balance or 'balance' not in current_balance:
                        raise ValueError("Failed to fetch current balance")
                    
                    # Get balance in base units
                    balance_base = int(current_balance.get('balance', '0'))
                    balance_decimal = balance_base / self.TOKEN_DECIMALS
                    
                    logger.info(f"""
                    Trade parameters:
                    USDC input (base units): {amount}
                    USDC input (decimal): {usdc_decimal:.4f}
                    Price per token: {price:.4f} USDC
                    Tokens to sell (decimal): {tokens_to_sell:.4f}
                    Tokens to sell (base): {tokens_to_sell_base}
                    Available balance (base): {balance_base}
                    Available balance (decimal): {balance_decimal:.4f}
                    Best bid: {best_bid}
                    """)

                    # Step 4: Create and execute order
                    logger.info("Creating signed order with explicit parameters")
                    order_args = OrderArgs(
                        token_id=token_id,
                        side="SELL",  # Explicit side declaration
                        price=float(best_bid),  # Use best bid price for immediate execution
                        size=float(tokens_to_sell),
                        fee_rate_bps=0
                    )
                    logger.info(f"Market order arguments: {order_args.__dict__}")


                    # Create order with explicit options
                    try:
                        # Create market order - simpler than limit order creation
                        signed_order = self.trader_service.client.create_order(order_args)
                        logger.info(f"Signed market order created: {signed_order}")
                    except Exception as create_error:
                        logger.error(f"Market order creation failed: {str(create_error)}", exc_info=True)
                        raise ValueError(f"Failed to create market order: {str(create_error)}")

                    # Post order with explicit headers
                    try:
                        # Post order
                        logger.info("Submitting market order")
                        response = self.trader_service.client.post_order(signed_order, OrderType.GTC)
                        logger.info(f"Market order submission response: {response}")
                            
                        if not response:
                            raise ValueError("Empty response from order submission")
                                
                        if isinstance(response, dict) and (response.get('error') or response.get('errorMsg')):
                            raise ValueError(f"Order submission failed: {response.get('error') or response.get('errorMsg')}")
                    except Exception as post_error:
                        logger.error(f"Market order submission failed: {str(post_error)}", exc_info=True)
                        raise ValueError(f"Failed to submit market order: {str(post_error)}")
                        
                    logger.info(f"Order post response: {response}")
                    
                    logger.info(f"Order successfully placed: {response}")

                    # After successful order, transfer proceeds to user
                    usdc_amount = int(usdc_decimal * 1_000_000)  # Convert back to base units
                    transfer_result = await self._transfer_proceeds_to_user(user_address, usdc_amount)
                    
                    return {
                        "success": True,
                        "order_id": response.get("orderID"),
                        "status": response.get("status"),
                        "details": {
                            "tokens_sold": tokens_to_sell,
                            "expected_usdc": usdc_decimal,
                            "price": price,
                            "best_bid": best_bid,
                            "transaction_hashes": response.get("transactionsHashes", [])
                        },
                        "transfer": transfer_result
                    }

                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"Attempt {attempt + 1} failed: {last_error}")
                    
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(3)  # Wait before retry
                        continue
                    raise ValueError(f"Failed after {MAX_RETRIES} attempts. Last error: {last_error}")

        except Exception as e:
            logger.error(f"Delegated sell execution failed: {str(e)}")
            raise ValueError(str(e))

    async def _transfer_proceeds_to_user(self, user_address: str, amount: int):
            """Transfer USDC proceeds to user's wallet using Web3Service"""
            try:
                result = await self.web3_service.transfer_usdc(user_address, amount)
                return result
            except Exception as e:
                raise ValueError(f"Failed to transfer proceeds: {str(e)}")