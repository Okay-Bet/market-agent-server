# src/services/trader_service.py
import time
import asyncio
from typing import Dict, Optional
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, MarketOrderArgs, BalanceAllowanceParams, AssetType
from py_clob_client.order_builder.constants import BUY, SELL
from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport
import ast
from ..config import PRIVATE_KEY, SUBGRAPH_URL, logger, EXCHANGE_ADDRESS
from ..models.api import Position
from .sell_service import SellService
from .web3_service import Web3Service
from .market_service import MarketService
from .postgres_service import PostgresService

class TraderService:
    def __init__(self):
        self.web3_service = Web3Service()
        self.postgres_service = PostgresService() 
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
        self.sell_service = SellService(self)

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

    def execute_trade(self, token_id: str, price: float, amount: float, side: str, is_yes_token: bool, user_address: str):
        """Execute a trade with proper order book verification and position recording"""
        try:
            # First get market info from the subgraph to map token_id to condition_id
            query = gql("""
                query GetMarketInfo($tokenId: ID!) {
                    tokenIdCondition(id: $tokenId) {
                        condition {
                            id
                        }
                        outcomeIndex
                    }
                }
            """)
            
            result = self.gql_client.execute(query, variable_values={
                "tokenId": token_id.lower()
            })
            
            # Extract condition_id and outcome from the result
            condition_id = result['tokenIdCondition']['condition']['id']
            outcome = result['tokenIdCondition']['outcomeIndex']
            
            # Your existing orderbook verification
            orderbook = self.client.get_order_book(token_id)
            if not orderbook:
                raise ValueError("Unable to fetch orderbook")
                
            # Convert bid/ask lists to floats for easier comparison
            bids = [(float(b.price), float(b.size)) for b in orderbook.bids] if orderbook.bids else []
            asks = [(float(a.price), float(a.size)) for a in orderbook.asks] if orderbook.asks else []

            # Execute trade based on side
            if side.upper() == "BUY":
                available_liquidity = sum(size for p, size in asks if p <= price)
                if not available_liquidity:
                    raise ValueError(f"No liquidity available at or below price {price}")
                result = self.execute_buy_trade(token_id, price, amount, is_yes_token, available_liquidity)
            else:
                available_liquidity = sum(size for p, size in bids if p >= price)
                if not available_liquidity:
                    raise ValueError(f"No liquidity available at or above price {price}")
                result = self.execute_sell_trade(token_id, price, amount, is_yes_token, available_liquidity)

            # If trade successful, record position
            if result.get('success'):
                try:
                    self.postgres_service.record_position({
                        'user_address': user_address,
                        'order_id': result['order_id'],
                        'token_id': token_id,
                        'condition_id': condition_id,
                        'outcome': int(outcome),
                        'amount': result.get('executed_amount'),
                        'price': result.get('executed_price'),
                        'side': side
                    })
                except Exception as db_error:
                    logger.error(f"Failed to record position in database: {str(db_error)}")
                    # Depending on your requirements, you might want to:
                    # - Roll back the trade
                    # - Mark it for retry
                    # - Or just log and continue
                    
            return result

        except Exception as e:
            logger.error(f"Trade execution failed: {str(e)}")
            raise e

    def execute_buy_trade(self, token_id: str, price: float, amount: float, is_yes_token: bool, available_liquidity: float):
        """
        Execute a buy trade using exact USDC amount from user
        
        Args:
            token_id: Market token identifier
            price: Target price per outcome token
            amount: Amount in decimal USDC (what user sent)
            is_yes_token: Whether this is a YES token position
            available_liquidity: Available liquidity (not used for market orders)
        """
        try:
            logger.info(f"""
            Buy Order Details:
            - USDC Amount to spend: {amount}
            - Target price: {price}
            - Available liquidity: {available_liquidity}
            """)
            
            # Store pre-trade orderbook state
            orderbook = self.client.get_order_book(token_id)
            best_ask = min([ask.price for ask in orderbook.asks]) if orderbook.asks else None
            
            order_args = MarketOrderArgs(
                token_id=token_id,
                amount=amount
            )
            
            signed_order = self.client.create_market_order(order_args)
            post_response = self.client.post_order(signed_order, OrderType.FOK)
            
            # Get post-trade orderbook and last trade price
            last_trade = self.client.get_last_trade_price(token_id)

            token_amount = amount / price if price > 0 else 0
            
            execution_details = {
                "success": True,
                "order_id": post_response.get("orderID"),
                "status": post_response.get("status"),
                "executed_price": last_trade.get("price") if last_trade else best_ask,
                "executed_amount": token_amount, # amount of tokens bought
                "original_amount": amount, # amount of usdc paid
                "original_price": price
            }
            
            logger.info(f"Trade Execution Details: {execution_details}")
            return execution_details
            
        except Exception as e:
            logger.error(f"Buy trade execution failed: {str(e)}")
            raise e

    async def execute_delegated_sell(self, token_id: str, price: float, amount: int, is_yes_token: bool, user_address: str):
            """Delegate sell execution to SellService
            Note: user_address parameter is kept optional for backward compatibility
            """
            return await self.sell_service.execute_delegated_sell(
                token_id=token_id,
                price=price,
                amount=amount,
                is_yes_token=is_yes_token,
                user_address=user_address
            )

    async def get_positions(self, user_address: Optional[str] = None):
        """
        Get positions with optional user filtering.
        When user_address is provided, returns only positions owned by that user.
        """
        try:
            # Get all agent positions from subgraph (source of truth)
            query = gql("""
                query Get_create_position_from_balancePositions($address: String!) {
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
            
            positions = []
            
            # If no user specified, return all positions
            if not user_address:
                for balance in result['userBalances']:
                    if int(balance['balance']) > 0:
                        position = await self._create_position_from_balance(balance)
                        positions.append(position)
                return positions

            # Get user ownership records
            user_positions = await self.postgres_service.get_user_positions(user_address)
            
            # Filter and enrich positions with user data
            for balance in result['userBalances']:
                if int(balance['balance']) > 0:
                    position = await self._create_position_from_balance(balance)
                    
                    # Find matching user position data
                    user_pos = next(
                        (p for p in user_positions 
                         if p['condition_id'] == balance['asset']['condition']['id'] and
                            p['outcome'] == int(balance['asset']['outcomeIndex'])),
                        None
                    )
                    
                    if user_pos:
                        position.entry_price = float(user_pos['entry_price'])
                        positions.append(position)

            return positions
                
        except Exception as e:
            logger.error(f"Error in get_positions: {str(e)}")
            raise ValueError(f"Failed to fetch positions: {str(e)}")

    def calculate_price_impact(self, token_id: str, amount: float, price: float, side: str, is_yes_token: bool = True) -> dict:
        """
        Calculate actual price impact and execution details based on orderbook depth.
        
        Args:
            token_id (str): Market token identifier
            amount (float): USDC amount in decimal format (e.g., 1.0 = 1 USDC)
            price (float): Target price per token
            side (str): "BUY" or "SELL"
            is_yes_token (bool): Not used anymore - kept for backward compatibility
        """
        try:
            logger.info(f"""
            Price Impact Calculation Started:
            - Token ID: {token_id}
            - Amount: {amount}
            - Price: {price}
            - Side: {side}
            """)

            # Basic input validation
            if amount <= 0:
                raise ValueError("Amount must be positive")
            if price <= 0 or price >= 1:
                raise ValueError("Price must be between 0 and 1")
            if side not in ["BUY", "SELL"]:
                raise ValueError("Side must be BUY or SELL")

            # Fetch orderbook
            orderbook = self.client.get_order_book(token_id)
            if not orderbook:
                raise ValueError("Unable to fetch orderbook")

            # Convert prices to floats
            asks = [(float(a.price), float(a.size)) for a in orderbook.asks] if orderbook.asks else []
            bids = [(float(b.price), float(b.size)) for b in orderbook.bids] if orderbook.bids else []
        

            # Calculate token amount needed
            token_amount = amount / price if price > 0 else 0
            logger.info(f"Calculated token amount: {token_amount}")

            # Determine which side of the book to use and price bounds
            if side == "BUY":
                orderbook_side = asks
                best_price = min([p for p, _ in asks]) if asks else float('inf')
                max_acceptable_price = price * 1.10  # 10% slippage tolerance
                acceptable_orders = [
                    order for order in orderbook_side 
                    if order[0] <= max_acceptable_price
                ]
            else:  # SELL
                orderbook_side = bids
                best_price = max([p for p, _ in bids]) if bids else 0
                min_acceptable_price = price * 0.90  # 10% slippage tolerance
                acceptable_orders = [
                    order for order in orderbook_side 
                    if order[0] >= min_acceptable_price
                ]

            logger.info(f"""
            Order Processing:
            - Using {'asks' if side == 'BUY' else 'bids'} side of orderbook
            - Target price: {price}
            - Best available price: {best_price}
            """)

            if not acceptable_orders:
                spread = abs(best_price - price) / price if price > 0 else float('inf')
                return {
                    "valid": False,
                    "error": f"Price spread too high ({spread:.1%}). Best available price is {best_price:.4f}",
                    "market_price": best_price
                }

            # Check available liquidity
            executable_liquidity = sum(size for _, size in acceptable_orders)
            logger.info(f"Executable liquidity: {executable_liquidity} vs needed: {token_amount}")

            if executable_liquidity < token_amount:
                return {
                    "valid": False,
                    "error": f"Insufficient liquidity: need {token_amount:.2f} tokens, only {executable_liquidity:.2f} available",
                    "market_price": best_price
                }

            # Calculate actual execution price
            remaining_tokens = token_amount
            total_cost = 0
            
            for level_price, level_size in sorted(acceptable_orders, key=lambda x: x[0], reverse=(side == "SELL")):
                tokens_from_level = min(remaining_tokens, level_size)
                total_cost += tokens_from_level * level_price
                remaining_tokens -= tokens_from_level
                if remaining_tokens <= 0:
                    break

            weighted_avg_price = total_cost / token_amount if token_amount > 0 else price
            price_impact = (weighted_avg_price - price) / price if price > 0 else 0

            logger.info(f"""
            Final Calculations:
            - Weighted Average Price: {weighted_avg_price}
            - Price Impact: {price_impact}
            - Total Cost: {total_cost}
            """)

            return {
                "valid": True,
                "token_amount": token_amount,
                "executable_liquidity": executable_liquidity,
                "weighted_avg_price": weighted_avg_price,
                "price_impact": price_impact,
                "execution_possible": True,
                "estimated_total": int(total_cost * 1_000_000),
                "market_price": best_price
            }

        except Exception as e:
            logger.error(f"Error calculating price impact: {str(e)}")
            return {
                "valid": False,
                "error": str(e)
            }
        
    async def _create_position_from_balance(self, balance: Dict) -> Position:
        """
        Helper method to create Position object from balance data.
        
        Args:
            balance: Dictionary containing asset and balance information from subgraph
            
        Returns:
            Position: Constructed position object with market data
        """
        token_id = balance['asset']['id']
        condition_id = balance['asset']['condition']['id']
        
        # Get market info asynchronously
        market_info = await MarketService.get_market(token_id)
        
        # Get current prices
        prices = self.get_orderbook_price(token_id)
        
        # Parse outcomes and create balance array
        outcome_count = len(ast.literal_eval(market_info["outcomes"]))
        balances = [0.0] * outcome_count
        outcome_index = int(balance['asset']['outcomeIndex'])
        balances[outcome_index] = float(balance['balance'])
        
        # Construct position object
        return Position(
            token_id=token_id,
            market_id=condition_id,
            market_question=market_info["question"],
            outcomes=ast.literal_eval(market_info["outcomes"]),
            prices=[float(p) for p in ast.literal_eval(market_info["outcome_prices"])],
            balances=balances
        )