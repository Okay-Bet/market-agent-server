# src/services/trader_service.py
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, MarketOrderArgs
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
        try:
            # Keep track of raw USDC units
            raw_usdc_amount = amount  # Already in USDC units
            decimal_usdc = raw_usdc_amount / 1_000_000  # For display only
            required_amount = int(amount * 1.02)  # Buffer in USDC units
            
            logger.info(f"Raw USDC units: {raw_usdc_amount}, Decimal USDC: {decimal_usdc}")
            
            # Check existing allowance (comparing raw units)
            allowance = int(self.web3_service.usdc.functions.allowance(
                self.web3_service.wallet_address,
                self.web3_service.w3.to_checksum_address(EXCHANGE_ADDRESS)
            ).call())
            
            if allowance < required_amount:
                logger.info(f"Insufficient allowance. Have {allowance} USDC units, need {required_amount} USDC units")
                try:
                    approval = self.web3_service.approve_usdc()
                    if not approval["success"]:
                        raise ValueError("Failed to approve USDC")
                except Exception as e:
                    logger.error(f"USDC approval failed: {str(e)}")
                    raise ValueError(f"Failed to approve USDC: {str(e)}")
            else:
                logger.info(f"Sufficient allowance exists: {allowance} >= {required_amount} USDC units")

            if side.upper() == "BUY":
                # Get orderbook and find best ask price
                orderbook = self.client.get_order_book(token_id)
                if not orderbook or not orderbook.asks:
                    raise ValueError("No asks available in orderbook")
                
                # Get best ask price from orderbook
                best_ask = float(orderbook.asks[0].price)
                logger.info(f"Best ask price from orderbook: {best_ask}")
                
                # Use input price since it's validated by check_price
                execution_price = price
                outcome_tokens = round(decimal_usdc / execution_price, 2)
                actual_usdc_units = int(outcome_tokens * execution_price * 1_000_000)
                
                logger.info(f"BUY order: {outcome_tokens} tokens at price {execution_price}")
                logger.info(f"Required USDC units: {actual_usdc_units}")

                MIN_TRADE_SIZE = 5.0
                if outcome_tokens < MIN_TRADE_SIZE:
                    min_usdc_needed = MIN_TRADE_SIZE * execution_price
                    raise ValueError(
                        f"Trade size too small. Minimum is {MIN_TRADE_SIZE} tokens, got {outcome_tokens:.2f}. "
                        f"Need at least {min_usdc_needed:.6f} USDC for this price."
                    )

                # Check balances using raw USDC units
                balance = int(self.web3_service.usdc.functions.balanceOf(
                    self.web3_service.wallet_address
                ).call())
                
                if balance < actual_usdc_units:
                    raise ValueError(
                        f"Insufficient USDC balance. Have: {balance/1_000_000:.2f} USDC, "
                        f"Need: {actual_usdc_units/1_000_000:.2f} USDC"
                    )

                # Check price considering yes/no token type
                self.check_price(token_id, float(price), side, is_yes_token)

                try:
                    # Create market order with simplified MarketOrderArgs
                    order_args = MarketOrderArgs(
                        token_id=token_id,
                        amount=float(outcome_tokens)  # Number of outcome tokens to buy
                    )
                    
                    logger.info(f"Creating market order with args: token_id={token_id}, amount={outcome_tokens}")
                    signed_order = self.client.create_market_order(order_args)
                    logger.info("Market order created successfully")

                    logger.info("Using FOK order type for market BUY order")
                    response = self.client.post_order(signed_order, OrderType.FOK)
                    logger.info(f"Order response: {response}")

                except Exception as e:
                    logger.error(f"Error with market order: {str(e)}")
                    raise e

            else:  # SELL orders
                outcome_tokens = round(decimal_usdc / float(price), 2)
                logger.info(f"SELL order: {outcome_tokens} tokens at {price} USDC/token")

                try:
                    order_args = OrderArgs(
                        token_id=token_id,
                        side=SELL,
                        price=float(price),
                        size=float(outcome_tokens)
                    )
                    
                    logger.info(f"Creating limit sell order with args: {order_args}")
                    signed_order = self.client.create_order(order_args)
                    logger.info("Limit order created successfully")

                    logger.info("Using GTC order type for limit SELL order")
                    response = self.client.post_order(signed_order, OrderType.GTC)
                    logger.info(f"Order response: {response}")

                except Exception as e:
                    logger.error(f"Error with limit order: {str(e)}")
                    raise e

            if response.get("errorMsg"):
                raise ValueError(f"Order placement failed: {response['errorMsg']}")

            return {
                "success": True,
                "order_id": response.get("orderID"),
                "status": response.get("status"),
                "balance_info": {
                    "balance_usdc": balance / 1_000_000,
                    "required_amount": actual_usdc_units / 1_000_000,
                    "has_sufficient_balance": balance >= actual_usdc_units,
                    "has_sufficient_allowance": allowance >= required_amount
                }
            }

        except Exception as e:
            logger.error(f"Trade execution failed: {str(e)}")
            raise e
    
    async def get_positions(self):
        try:
            transport = RequestsHTTPTransport(url=SUBGRAPH_URL)
            client = Client(transport=transport, fetch_schema_from_transport=True)
            
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
            
            result = client.execute(query, variable_values={
                "address": self.web3_service.wallet_address.lower()
            })
            
            positions = []
            for balance in result['userBalances']:
                if int(balance['balance']) > 0:
                    market_info = await MarketService.get_market(balance['asset']['id'])
                    prices = self.get_orderbook_price(balance['asset']['id'])
                    
                    positions.append(Position(
                        market_id=str(market_info["id"]),
                        token_id=balance['asset']['id'],
                        market_question=market_info["question"],
                        outcomes=ast.literal_eval(market_info["outcomes"]),
                        prices=[float(p) for p in ast.literal_eval(market_info["outcome_prices"])],
                        balances=[int(balance['balance']) / 1e18 if i == int(balance['asset']['outcomeIndex']) else 0.0 
                                for i in range(len(ast.literal_eval(market_info["outcomes"])))]
                    ))
                    
            return positions
        except Exception as e:
            logger.error(f"Error fetching positions from subgraph: {str(e)}")
            raise ValueError(f"Failed to fetch positions: {str(e)}")
