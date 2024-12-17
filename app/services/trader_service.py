# src/services/trader_service.py
import time
import asyncio
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, MarketOrderArgs, BalanceAllowanceParams, AssetType
from py_clob_client.order_builder.constants import BUY, SELL
from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport
import ast
from ..config import PRIVATE_KEY, SUBGRAPH_URL, logger, EXCHANGE_ADDRESS
from ..models import Position
from .web3_service import Web3Service
from .market_service import MarketService

class TraderService:
    def __init__(self):
        self.web3_service = Web3Service()
        self.client = ClobClient(
            "https://clob.polymarket.com",
            key=PRIVATE_KEY,
            chain_id=137,
            signature_type=0
        )
        self.credentials = self.client.create_or_derive_api_creds()
        self.client.set_api_creds(self.credentials)

         # Initialize GQL client for subgraph
        transport = RequestsHTTPTransport(url=SUBGRAPH_URL)
        self.gql_client = Client(transport=transport, fetch_schema_from_transport=True)


    def get_orderbook_price(self, token_id: str):
        try:
            orderbook = self.client.get_order_book(token_id)
            bid_price = float(orderbook.bids[0].price) if orderbook.bids else 0.0
            ask_price = float(orderbook.asks[0].price) if orderbook.asks else 0.0
            return [bid_price, ask_price]
        except Exception as e:
            logger.error(f"Error getting price for token {token_id}: {str(e)}")
            return [0.0, 0.0]

    def check_balances(self, amount: float, price: float):
        """
        Check if there are sufficient USDC balances and allowances for the trade.
        All inputs should be in decimal USDC format (e.g., 0.5 USDC, not 500000)
        
        Args:
            amount (float): The USDC amount in decimal format
            price (float): The price in decimal format
        """
        try:
            # Keep all calculations in raw USDC units (multiplied by 1_000_000)
            usdc_amount_needed = int(float(amount) * float(price) * 1_000_000)
            usdc_amount_with_buffer = int(usdc_amount_needed * 1.02)  # Add 2% buffer

            # Get raw balance and allowance from chain (these are already in USDC units)
            balance = int(self.web3_service.usdc.functions.balanceOf(
                self.web3_service.wallet_address
            ).call())
            
            allowance = int(self.web3_service.usdc.functions.allowance(
                self.web3_service.wallet_address,
                self.web3_service.w3.to_checksum_address(EXCHANGE_ADDRESS)
            ).call())

            # Convert to decimal USDC only for return values
            balance_usdc = float(balance) / 1_000_000
            allowance_usdc = float(allowance) / 1_000_000
            required_amount_usdc = float(usdc_amount_with_buffer) / 1_000_000
            
            # Compare raw values in USDC units
            has_sufficient_balance = balance >= usdc_amount_with_buffer
            has_sufficient_allowance = allowance >= usdc_amount_with_buffer

            logger.info(f"Balance check - Have: {balance} USDC units, Need: {usdc_amount_with_buffer} USDC units")
            
            return {
                "balance_usdc": balance_usdc,
                "allowance_usdc": allowance_usdc,
                "required_amount": required_amount_usdc,
                "has_sufficient_balance": has_sufficient_balance,
                "has_sufficient_allowance": has_sufficient_allowance
            }
        except Exception as e:
            logger.error(f"Error checking balances: {str(e)}")
            raise ValueError(f"Failed to check balances: {str(e)}")

    def check_price(self, token_id: str, expected_price: float, side: str, is_yes_token: bool):
        """
        Validates if the requested order price is within acceptable range of market price.
        
        Args:
            token_id: The market token ID
            expected_price: The price we want to trade at
            side: "BUY" or "SELL"
            is_yes_token: Whether this is a YES or NO token
        """
        try:
            orderbook = self.client.get_order_book(token_id)
            
            logger.info(f"Raw orderbook data - Bids: {orderbook.bids}, Asks: {orderbook.asks}")
            
            # Convert to float and handle empty orderbooks
            bids = [float(bid.price) for bid in orderbook.bids] if orderbook.bids else []
            asks = [float(ask.price) for ask in orderbook.asks] if orderbook.asks else []
            
            # Get best bid/ask
            best_bid = max(bids) if bids else None
            best_ask = min(asks) if asks else None
            
            logger.info(f"Best bid: {best_bid}, Best ask: {best_ask}")
            
            # For NO tokens, we need to invert the prices (1 - price)
            if not is_yes_token:
                expected_price = 1 - expected_price
                if best_bid is not None:
                    best_bid = 1 - best_bid
                if best_ask is not None:
                    best_ask = 1 - best_ask
                logger.info(f"NO token - Adjusted prices - Expected: {expected_price}, Best bid: {best_bid}, Best ask: {best_ask}")

            # If selling, compare with bid (lower price)
            if side == "SELL":
                if not best_bid:
                    raise ValueError("No buy orders available in orderbook")
                market_price = best_bid
                # Allow selling at higher prices
                if expected_price < market_price * 0.99:  # 1% tolerance
                    raise ValueError(f"Sell price too low. Your price: {expected_price:.3f}, Market price: {market_price:.3f}")
                    
            # If buying, compare with ask (higher price)
            else:  # BUY
                if not best_ask:
                    raise ValueError("No sell orders available in orderbook")
                market_price = best_ask
                # Allow buying at lower prices
                if expected_price > market_price * 1.01:  # 1% tolerance
                    raise ValueError(f"Buy price too high. Your price: {expected_price:.3f}, Market price: {market_price:.3f}")

            return True

        except Exception as e:
            logger.error(f"Error checking price for token {token_id}: {str(e)}")
            raise e

    def execute_trade(self, token_id: str, price: float, amount: float, side: str, is_yes_token: bool):
        """Execute a trade with proper order book verification"""
        try:
            # Verify order book and liquidity first
            orderbook = self.client.get_order_book(token_id)
            if not orderbook:
                raise ValueError("Unable to fetch orderbook")
                
            # Convert bid/ask lists to floats for easier comparison
            bids = [(float(b.price), float(b.size)) for b in orderbook.bids] if orderbook.bids else []
            asks = [(float(a.price), float(a.size)) for a in orderbook.asks] if orderbook.asks else []
            
            logger.info(f"""
            Order Book State:
            - Bid count: {len(bids)}
            - Ask count: {len(asks)}
            - Best bid: {max(bid[0] for bid in bids) if bids else None}
            - Best ask: {min(ask[0] for ask in asks) if asks else None}
            """)

            # Check available liquidity at our price level
            if side.upper() == "BUY":
                available_liquidity = sum(size for p, size in asks if p <= price)
                if not available_liquidity:
                    raise ValueError(f"No liquidity available at or below price {price}")
                logger.info(f"Available buy liquidity at {price}: {available_liquidity}")
                return self.execute_buy_trade(token_id, price, amount, is_yes_token, available_liquidity)
            else:
                available_liquidity = sum(size for p, size in bids if p >= price)
                if not available_liquidity:
                    raise ValueError(f"No liquidity available at or above price {price}")
                logger.info(f"Available sell liquidity at {price}: {available_liquidity}")
                return self.execute_sell_trade(token_id, price, amount, is_yes_token, available_liquidity)

        except Exception as e:
            logger.error(f"Trade execution failed: {str(e)}")
            raise e

    def execute_buy_trade(self, token_id: str, price: float, amount: float, is_yes_token: bool, available_liquidity: float):
        """Execute a buy trade with price-adjusted allowance calculations"""
        MAX_RETRIES = 3
        RETRY_DELAY = 2
        
        try:
            USDC_DECIMALS = 6
            
            # Adjust fee buffer based on price - lower prices need higher buffers
            # This is because of how market maker systems handle risk
            if price <= 0.1:
                FEE_BUFFER = 1.15  # 15% buffer for very low prices
            elif price <= 0.5:
                FEE_BUFFER = 1.08  # 8% buffer for medium-low prices
            elif price <= 0.9:
                FEE_BUFFER = 1.05  # 5% buffer for medium-high prices
            else:
                FEE_BUFFER = 1.02  # 2% buffer for high prices
                
            # Calculate base amounts
            usdc_decimal = amount / (10 ** USDC_DECIMALS)
            outcome_tokens = float(usdc_decimal / price)
            
            # Add price-adjusted safety margin to USDC requirements
            price_factor = 1 + ((1 - price) * 0.5)  # Higher adjustment for lower prices
            base_usdc_needed = int(outcome_tokens * price * (10 ** USDC_DECIMALS))
            actual_usdc_needed = int(base_usdc_needed * FEE_BUFFER * price_factor)
            
            logger.info(f"""
            Buy Order Calculations:
            - USDC amount: {usdc_decimal}
            - Price per token: {price}
            - Price factor: {price_factor}
            - Fee buffer: {FEE_BUFFER}
            - Outcome tokens: {outcome_tokens}
            - Available liquidity: {available_liquidity}
            - Base USDC needed: {base_usdc_needed / (10 ** USDC_DECIMALS)}
            - Actual USDC needed: {actual_usdc_needed / (10 ** USDC_DECIMALS)}
            """)

            # Verify against available liquidity with price-adjusted buffer
            if outcome_tokens > available_liquidity * 0.95:  # Add 5% safety margin
                raise ValueError(f"Insufficient liquidity. Need {outcome_tokens} tokens but only {available_liquidity} available")

            # Balance and allowance check with adjusted amounts
            for attempt in range(MAX_RETRIES):
                try:
                    balance = int(self.web3_service.usdc.functions.balanceOf(
                        self.web3_service.wallet_address
                    ).call())
                    
                    allowance = int(self.web3_service.usdc.functions.allowance(
                        self.web3_service.wallet_address,
                        self.web3_service.w3.to_checksum_address(EXCHANGE_ADDRESS)
                    ).call())
                    
                    logger.info(f"""
                    Balance check (attempt {attempt + 1}):
                    - Balance: {balance / (10 ** USDC_DECIMALS)} USDC
                    - Required: {actual_usdc_needed / (10 ** USDC_DECIMALS)} USDC
                    - Price-adjusted buffer: {price_factor}
                    """)
                    
                    if balance < actual_usdc_needed:
                        raise ValueError(f"Insufficient balance. Have: {balance / (10 ** USDC_DECIMALS):.6f} USDC, Need: {actual_usdc_needed / (10 ** USDC_DECIMALS):.6f} USDC")
                    
                    if allowance < actual_usdc_needed:
                        logger.info("Insufficient allowance, requesting approval")
                        approval = self.web3_service.approve_usdc()
                        if not approval["success"]:
                            raise ValueError("Failed to approve USDC")
                        time.sleep(RETRY_DELAY)
                        continue
                    
                    break
                    
                except Exception as e:
                    if attempt == MAX_RETRIES - 1:
                        raise ValueError(f"Failed to validate balance/allowance after {MAX_RETRIES} attempts: {str(e)}")
                    time.sleep(RETRY_DELAY)

            # Execute order with market order
            for attempt in range(MAX_RETRIES):
                try:
                    order_args = MarketOrderArgs(
                        token_id=token_id,
                        amount=float(outcome_tokens)
                    )
                    
                    logger.info(f"""
                    Submitting buy order (attempt {attempt + 1}):
                    - Token ID: {token_id}
                    - Amount (tokens): {outcome_tokens}
                    - Price: {price}
                    - Adjusted allowance: {actual_usdc_needed / (10 ** USDC_DECIMALS)}
                    """)
                    
                    signed_order = self.client.create_market_order(order_args)
                    response = self.client.post_order(signed_order, OrderType.FOK)
                    
                    if response.get("errorMsg"):
                        raise ValueError(f"Order placement failed: {response['errorMsg']}")
                    
                    return {
                        "success": True,
                        "order_id": response.get("orderID"),
                        "status": response.get("status"),
                        "balance_info": {
                            "balance_usdc": balance / (10 ** USDC_DECIMALS),
                            "base_amount": base_usdc_needed / (10 ** USDC_DECIMALS),
                            "total_with_fees": actual_usdc_needed / (10 ** USDC_DECIMALS)
                        }
                    }
                    
                except Exception as e:
                    if attempt == MAX_RETRIES - 1:
                        raise ValueError(f"Failed to execute trade after {MAX_RETRIES} attempts: {str(e)}")
                    logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")
                    time.sleep(RETRY_DELAY)

        except Exception as e:
            logger.error(f"Buy trade execution failed: {str(e)}")
            raise e

    async def execute_delegated_sell(self, token_id: str, price: float, amount: int, is_yes_token: bool):
        """
        Execute a delegated sell order with complete approval checks, balance verification,
        and proper order execution.
        
        Args:
            token_id: The market token ID
            price: The selling price
            amount: Amount in USDC base units
            is_yes_token: Whether this is a YES token
        """
        try:
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
            orderbook = self.client.get_order_book(token_id)
            if not orderbook.bids:
                raise ValueError("No buy orders available in orderbook")
            
            best_bid = float(orderbook.bids[0].price)
            logger.info(f"Best bid price: {best_bid}")

            if float(price) < best_bid * 0.99:  # 1% tolerance
                raise ValueError(f"Sell price ({price}) too low compared to best bid ({best_bid})")

            # Step 3: Calculate amounts and verify balance
            usdc_decimal = float(amount) / 1_000_000  # Convert from base units
            tokens_to_sell = usdc_decimal / float(price)
            
            # Update and verify balance allowance
            MAX_RETRIES = 3
            last_error = None
            
            for attempt in range(MAX_RETRIES):
                try:
                    # Set up balance params
                    balance_params = BalanceAllowanceParams(
                        asset_type=AssetType.CONDITIONAL,
                        token_id=token_id,
                        signature_type=0
                    )
                    
                    # Force balance allowance update
                    logger.info(f"Updating balance allowance (attempt {attempt + 1})")
                    updated_balance = self.client.update_balance_allowance(balance_params)
                    await asyncio.sleep(2)
                    
                    # Verify balance
                    current_balance = self.client.get_balance_allowance(balance_params)
                    logger.info(f"Current balance state: {current_balance}")
                    
                    if not current_balance or 'balance' not in current_balance:
                        raise ValueError("Failed to fetch current balance")
                    
                    balance = float(current_balance.get('balance', '0'))
                    
                    if balance <= 0:
                        raise ValueError("Insufficient token balance for trade")
                    
                    if tokens_to_sell > balance:
                        raise ValueError(
                            f"Insufficient balance. Have: {balance}, Need: {tokens_to_sell}"
                        )
                    
                    logger.info(f"""
                    Trade parameters:
                    USDC desired: {usdc_decimal}
                    Price per token: {price}
                    Best bid price: {best_bid}
                    Tokens to sell: {tokens_to_sell}
                    Available balance: {balance}
                    """)

                    # Step 4: Create and execute order
                    order_args = OrderArgs(
                        token_id=token_id,
                        side=SELL,
                        price=float(price),
                        size=float(tokens_to_sell),
                        fee_rate_bps=0,
                        nonce=0,
                        expiration=0
                    )
                    
                    logger.info("Creating signed order")
                    signed_order = self.client.create_order(order_args)
                    
                    logger.info("Submitting order with GTC type")
                    response = self.client.post_order(signed_order, OrderType.GTC)
                    
                    if response.get("errorMsg"):
                        raise ValueError(f"Order placement failed: {response['errorMsg']}")
                    
                    logger.info(f"Order successfully placed: {response}")
                    
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
                        }
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
    
    async def get_positions(self):
        try:
            query = gql("""
                query GetPositions($address: String!) {
                    userBalances(where: {user: $address}) {
                        asset {
                            id
                            condition {
                                id
                            }
                            outcomeIndex
                        }
                        balance
                        user
                    }
                }
            """)
                
            result = self.gql_client.execute(query, variable_values={
                "address": self.web3_service.wallet_address.lower()
            })
            
            print(f"Raw query result: {result}")  # Debug log
            
            positions = []
            for balance in result['userBalances']:
                if int(balance['balance']) > 0:
                    print(f"Processing balance: {balance}")
                    market_info = await MarketService.get_market(balance['asset']['id'])
                    print(f"Market info: {market_info}")
                    prices = self.get_orderbook_price(balance['asset']['id'])
                    print(f"Current prices: {prices}")
                    
                    # Create balance array with proper positioning
                    outcome_count = len(ast.literal_eval(market_info["outcomes"]))
                    balances = [0.0] * outcome_count
                    outcome_index = int(balance['asset']['outcomeIndex'])
                    raw_balance = int(balance['balance'])
                    converted_balance = raw_balance / 1e6
                    
                    print(f"Converting balance {raw_balance} to {converted_balance}")
                    
                    balances[outcome_index] = converted_balance
                    
                    position = Position(
                        market_id=str(market_info["id"]),
                        token_id=balance['asset']['id'],
                        market_question=market_info["question"],
                        outcomes=ast.literal_eval(market_info["outcomes"]),
                        prices=[float(p) for p in ast.literal_eval(market_info["outcome_prices"])],
                        balances=balances,
                        # These will use the default None values
                        entry_prices=None,
                        timestamp=None
                    )
                    print(f"Created position: {position}")
                    positions.append(position)
                        
            print(f"Final positions: {positions}")
            return positions
                
        except Exception as e:
            print(f"Error in get_positions: {str(e)}")
            raise ValueError(f"Failed to fetch positions: {str(e)}")