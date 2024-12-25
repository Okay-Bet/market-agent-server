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
from ..models.api import Position
from .sell_service import SellService
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
        """
        Execute a buy trade with the exact USDC amount received from user
        
        Args:
            token_id (str): Market token identifier
            price (float): Target price per outcome token
            amount (float): Amount in decimal USDC
            is_yes_token (bool): Whether this is a YES token
            available_liquidity (float): Available liquidity at the price level
        """
        try:
            # Calculate the number of outcome tokens to buy
            token_amount = amount / price
            
            logger.info(f"""
            Buy Order Details:
            - Token Amount: {token_amount}
            - USDC Amount: {amount}
            - Price: {price}
            - Token ID: {token_id}
            """)

            # Create and execute market order
            order_args = MarketOrderArgs(
                token_id=token_id,
                amount=float(token_amount)
            )
            
            signed_order = self.client.create_market_order(order_args)
            if not signed_order:
                raise ValueError("Failed to create signed order")
                
            response = self.client.post_order(signed_order, OrderType.FOK)
            if not response:
                raise ValueError("No response received from order placement")
            
            if response.get("errorMsg"):
                raise ValueError(f"Order placement failed: {response['errorMsg']}")
            
            return {
                "success": True,
                "order_id": response.get("orderID"),
                "status": response.get("status")
            }

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
                                        
                    balances[outcome_index] = raw_balance
                    
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

    def calculate_price_impact(self, token_id: str, amount: float, price: float, side: str) -> dict:
        """
        Calculate actual price impact and execution details based on orderbook depth.
        
        Args:
            token_id (str): Market token identifier
            amount (float): USDC amount in decimal format (e.g., 1.0 = 1 USDC)
            price (float): Target price per token
            side (str): "BUY" or "SELL"
            
        Returns:
            dict: Detailed impact analysis containing:
                - token_amount: Number of tokens to trade
                - available_liquidity: Total available liquidity at any price
                - executable_liquidity: Available liquidity at acceptable prices
                - weighted_avg_price: Expected average execution price
                - price_impact: Calculated price impact as a decimal
                - execution_possible: Whether full execution is possible
                - estimated_total: Estimated total USDC needed including impact
                - levels_used: Number of orderbook levels needed
                
        Raises:
            ValueError: If orderbook can't be fetched or invalid parameters
        """
        try:
            # Input validation
            if amount <= 0:
                raise ValueError("Amount must be positive")
            if price <= 0:
                raise ValueError("Price must be positive")
            if side not in ["BUY", "SELL"]:
                raise ValueError("Side must be BUY or SELL")

            # Fetch and validate orderbook
            orderbook = self.client.get_order_book(token_id)
            if not orderbook:
                raise ValueError("Unable to fetch orderbook")

            # Initialize analysis variables
            token_amount = amount / price  # How many tokens we want
            total_available_liquidity = 0
            executable_liquidity = 0
            total_cost = 0
            levels_used = 0
            
            logger.info(f"""
            Starting Price Impact Calculation:
            - Token ID: {token_id}
            - USDC Amount: {amount}
            - Target Price: {price}
            - Side: {side}
            - Tokens Needed: {token_amount}
            """)

            if side == "BUY":
                # For buys, we analyze the ask side of the book
                # Convert all asks to float and sort by price
                asks = [(float(a.price), float(a.size)) for a in orderbook.asks]
                asks.sort(key=lambda x: x[0])  # Sort by price ascending
                
                remaining_tokens = token_amount
                max_acceptable_price = price * 1.50  # Allow up to 50% price impact
                
                for level_price, level_size in asks:
                    total_available_liquidity += level_size
                    
                    # Skip levels with prices too high
                    if level_price > max_acceptable_price:
                        continue
                        
                    executable_liquidity += level_size
                    
                    if remaining_tokens > 0:
                        # Calculate how many tokens we can take from this level
                        tokens_from_level = min(remaining_tokens, level_size)
                        total_cost += tokens_from_level * level_price
                        remaining_tokens -= tokens_from_level
                        levels_used += 1
                        
                        logger.debug(f"""
                        Processing Level:
                        - Price: {level_price}
                        - Size: {level_size}
                        - Used: {tokens_from_level}
                        - Remaining Needed: {remaining_tokens}
                        """)

            else:  # SELL side
                # For sells, we analyze the bid side of the book
                bids = [(float(b.price), float(b.size)) for b in orderbook.bids]
                bids.sort(key=lambda x: x[0], reverse=True)  # Sort by price descending
                
                remaining_tokens = token_amount
                min_acceptable_price = price * 0.50  # Allow up to 50% price impact
                
                for level_price, level_size in bids:
                    total_available_liquidity += level_size
                    
                    # Skip levels with prices too low
                    if level_price < min_acceptable_price:
                        continue
                        
                    executable_liquidity += level_size
                    
                    if remaining_tokens > 0:
                        tokens_from_level = min(remaining_tokens, level_size)
                        total_cost += tokens_from_level * level_price
                        remaining_tokens -= tokens_from_level
                        levels_used += 1

            # Calculate weighted average price and impact
            tokens_executed = token_amount - remaining_tokens
            if tokens_executed > 0:
                weighted_avg_price = total_cost / tokens_executed
                price_impact = abs(weighted_avg_price - price) / price
            else:
                weighted_avg_price = price
                price_impact = 0

            result = {
                "token_amount": token_amount,
                "available_liquidity": total_available_liquidity,
                "executable_liquidity": executable_liquidity,
                "weighted_avg_price": weighted_avg_price,
                "price_impact": price_impact,
                "execution_possible": executable_liquidity >= token_amount,
                "estimated_total": total_cost if tokens_executed == token_amount else amount * (1 + price_impact),
                "levels_used": levels_used,
                "remaining_tokens": remaining_tokens
            }
            
            logger.info(f"""
            Price Impact Analysis Result:
            - Available Liquidity: {result['available_liquidity']:.6f}
            - Executable Liquidity: {result['executable_liquidity']:.6f}
            - Weighted Avg Price: {result['weighted_avg_price']:.6f}
            - Price Impact: {result['price_impact']*100:.2f}%
            - Execution Possible: {result['execution_possible']}
            - Estimated Total USDC: {result['estimated_total']:.6f}
            - Orderbook Levels Used: {result['levels_used']}
            """)
            
            return result

        except Exception as e:
            logger.error(f"Error calculating price impact: {str(e)}")
            raise ValueError(f"Failed to calculate price impact: {str(e)}")