# src/services/trader_service.py
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL
from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport
import ast
from ..config import PRIVATE_KEY, SUBGRAPH_URL, logger
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
        usdc_amount_needed = amount * price
        usdc_amount_with_buffer = usdc_amount_needed * 1.02

        balance = self.web3_service.usdc.functions.balanceOf(self.web3_service.wallet_address).call()
        balance_usdc = balance / 1e6
        
        allowance = self.web3_service.usdc.functions.allowance(
            self.web3_service.wallet_address,
            self.web3_service.w3.to_checksum_address(EXCHANGE_ADDRESS)
        ).call()
        allowance_usdc = allowance / 1e6
        
        return {
            "balance_usdc": balance_usdc,
            "allowance_usdc": allowance_usdc,
            "required_amount": usdc_amount_with_buffer,
            "has_sufficient_balance": balance_usdc >= usdc_amount_with_buffer,
            "has_sufficient_allowance": allowance_usdc >= usdc_amount_with_buffer
        }

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

    def execute_trade(self, market_id: str, price: float, amount: float, side: str):
        try:
            balance_check = self.check_balances(amount, price)
            
            if not balance_check["has_sufficient_balance"]:
                raise ValueError(f"Insufficient USDC balance. Have: {balance_check['balance_usdc']:.2f} USDC")
            
            if not balance_check["has_sufficient_allowance"]:
                approval = self.web3_service.approve_usdc()
                if not approval["success"]:
                    raise ValueError("Failed to approve USDC")
                
                new_balance_check = self.check_balances(amount, price)
                if not new_balance_check["has_sufficient_allowance"]:
                    raise ValueError("USDC approval failed to increase allowance")

            self.check_price(market_id, price, side)

            order_args = OrderArgs(
                price=price,
                size=amount,
                side=BUY if side.upper() == "BUY" else SELL,
                token_id=market_id
            )
            
            signed_order = self.client.create_order(order_args)
            response = self.client.post_order(signed_order, OrderType.FOK)
            
            return {
                "success": True,
                "order_id": response.get("orderID"),
                "status": response.get("status"),
                "balance_info": balance_check
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
