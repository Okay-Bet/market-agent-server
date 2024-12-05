from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL
from pydantic import BaseModel
from typing import List, Optional
import os
import ast
import httpx
from dotenv import load_dotenv
import logging
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
import time
from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PRIVATE_KEY = os.getenv("POLYGON_WALLET_PRIVATE_KEY")
if not PRIVATE_KEY:
    raise ValueError("POLYGON_WALLET_PRIVATE_KEY not set in environment")

class OrderRequest(BaseModel):
    market_id: str
    price: float
    amount: float
    side: str

class Position(BaseModel):
    market_id: str
    token_id: str
    market_question: str
    outcomes: List[str]
    prices: List[float]
    balances: List[float]

class SellPositionRequest(BaseModel):
    token_id: str
    amount: float

class ServerTrader:
    def __init__(self):
        # Initialize Web3 with PoA middleware
        self.polygon_rpc = "https://polygon-rpc.com"
        self.w3 = Web3(Web3.HTTPProvider(self.polygon_rpc))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        
        # Initialize API endpoints
        self.gamma_url = "https://gamma-api.polymarket.com"
        self.gamma_markets_endpoint = self.gamma_url + "/markets"
        
        # Initialize CLOB client
        self.client = ClobClient(
            "https://clob.polymarket.com",
            key=PRIVATE_KEY,
            chain_id=137,
            signature_type=0
        )
        self.credentials = self.client.create_or_derive_api_creds()
        self.client.set_api_creds(self.credentials)

        # Contract addresses
        self.usdc_address = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
        self.ctf_address = Web3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")
        self.exchange_address = Web3.to_checksum_address("0x4bfb41d5B3570defd03c39a9A4d8de6bd8b8982e")
        
        # Initialize USDC contract
        self.usdc_abi = [
            {"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
            {"inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
            {"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"}
        ]
        
        # Initialize CTF contract ABI
        self.ctf_abi = [
            {
                "inputs": [
                    {"name": "account", "type": "address"},
                    {"name": "id", "type": "uint256"}
                ],
                "name": "balanceOf",
                "outputs": [{"name": "", "type": "uint256"}],
                "stateMutability": "view",
                "type": "function"
            }
        ]
        
        # Initialize contracts
        self.usdc = self.w3.eth.contract(address=self.usdc_address, abi=self.usdc_abi)
        self.ctf = self.w3.eth.contract(address=self.ctf_address, abi=self.ctf_abi)
        
        # Set wallet address
        self.wallet_address = self.w3.eth.account.from_key(PRIVATE_KEY).address
        
        logger.info(f"Server trader initialized with wallet {self.wallet_address}")

    def get_market(self, token_id: str) -> dict:
        """Get market information for a specific token ID"""
        params = {"clob_token_ids": token_id}
        res = httpx.get(self.gamma_markets_endpoint, params=params)
        if res.status_code == 200:
            data = res.json()
            if data:
                market = data[0]
                return {
                    "id": int(market["id"]),
                    "question": market["question"],
                    "outcomes": str(market["outcomes"]),
                    "outcome_prices": str(market["outcomePrices"]),
                }
        raise ValueError(f"Could not fetch market data for token {token_id}")

    def get_orderbook_price(self, token_id: str) -> List[float]:
        """Get current bid and ask prices from orderbook"""
        try:
            orderbook = self.client.get_order_book(token_id)
            bid_price = float(orderbook.bids[0].price) if orderbook.bids else 0.0
            ask_price = float(orderbook.asks[0].price) if orderbook.asks else 0.0
            return [bid_price, ask_price]
        except Exception as e:
            logger.error(f"Error getting price for token {token_id}: {str(e)}")
            return [0.0, 0.0]

    def check_balances(self, amount: float, price: float) -> dict:
        """Check USDC balance and allowance for the trade amount"""
        usdc_amount_needed = amount * price
        usdc_amount_with_buffer = usdc_amount_needed * 1.02  # 2% buffer

        balance = self.usdc.functions.balanceOf(self.wallet_address).call()
        balance_usdc = balance / 1e6
        
        allowance = self.usdc.functions.allowance(self.wallet_address, self.exchange_address).call()
        allowance_usdc = allowance / 1e6
        
        logger.info(f"Balance check details:")
        logger.info(f"USDC Balance: {balance_usdc} USDC")
        logger.info(f"Current allowance: {allowance_usdc} USDC")
        logger.info(f"Required amount: {usdc_amount_needed} USDC (with buffer: {usdc_amount_with_buffer} USDC)")
        
        return {
            "balance_usdc": balance_usdc,
            "allowance_usdc": allowance_usdc,
            "required_amount": usdc_amount_with_buffer,
            "has_sufficient_balance": balance_usdc >= usdc_amount_with_buffer,
            "has_sufficient_allowance": allowance_usdc >= usdc_amount_with_buffer
        }

    def approve_usdc(self) -> dict:
        """Approve USDC spending for the exchange"""
        try:
            logger.info("Starting USDC approval process...")
            max_amount = int("0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff", 16)
            
            # Get current gas prices
            base_fee = self.w3.eth.get_block('latest')['baseFeePerGas']
            priority_fee = 30_000_000_000  # 30 gwei
            max_fee = base_fee * 3 + priority_fee
            
            txn = self.usdc.functions.approve(
                self.exchange_address,
                max_amount
            ).build_transaction({
                'chainId': 137,
                'gas': 100000,
                'maxFeePerGas': max_fee,
                'maxPriorityFeePerGas': priority_fee,
                'nonce': self.w3.eth.get_transaction_count(self.wallet_address),
                'from': self.wallet_address
            })
            
            signed_txn = self.w3.eth.account.sign_transaction(txn, PRIVATE_KEY)
            tx_hash = self.w3.eth.send_raw_transaction(signed_txn.raw_transaction)
            logger.info(f"Approval transaction sent: {tx_hash.hex()}")
            
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
            if receipt['status'] != 1:
                raise ValueError("Approval transaction failed")
                
            logger.info("Approval transaction confirmed")
            return {
                "success": True,
                "tx_hash": receipt['transactionHash'].hex()
            }
            
        except Exception as e:
            logger.error(f"USDC approval failed: {str(e)}")
            raise ValueError(f"Failed to approve USDC: {str(e)}")

    def check_price(self, market_id: str, expected_price: float, side: str) -> bool:
        """Verify the current market price matches expected price"""
        orderbook = self.client.get_order_book(market_id)
        
        if side.upper() == "BUY":
            current_price = float(orderbook.asks[0].price) if orderbook.asks else None
        else:
            current_price = float(orderbook.bids[0].price) if orderbook.bids else None
        
        if current_price is None:
            raise ValueError(f"No {'sell' if side.upper() == 'BUY' else 'buy'} orders available")
        
        price_diff = abs(current_price - expected_price) / expected_price
        if price_diff > 0.01:  # 1% price deviation tolerance
            raise ValueError(f"Price deviation too high. Expected: {expected_price}, Current: {current_price}")
        
        return True

    def execute_trade(self, market_id: str, price: float, amount: float, side: str) -> dict:
        """Execute a trade with the given parameters"""
        try:
            # Check balance and allowance
            balance_check = self.check_balances(amount, price)
            logger.info(f"Balance check results: {balance_check}")
            
            usdc_amount_needed = amount * price
            if not balance_check["has_sufficient_balance"]:
                raise ValueError(f"Insufficient USDC balance. Have: {balance_check['balance_usdc']:.2f} USDC, Need: {usdc_amount_needed:.2f} USDC")
            
            if not balance_check["has_sufficient_allowance"]:
                logger.info("Insufficient allowance, attempting to approve USDC...")
                approval = self.approve_usdc()
                if not approval["success"]:
                    raise ValueError("Failed to approve USDC")
                
                # Wait and verify approval
                logger.info("Verifying approval...")
                new_balance_check = self.check_balances(amount, price)
                if not new_balance_check["has_sufficient_allowance"]:
                    raise ValueError("USDC approval failed to increase allowance")
                
                logger.info("USDC approved successfully")

            # Verify price hasn't moved significantly
            logger.info("Checking market price...")
            self.check_price(market_id, price, side)

            # Create and submit order
            logger.info("Creating order...")
            order_args = OrderArgs(
                price=price,
                size=amount,
                side=BUY if side.upper() == "BUY" else SELL,
                token_id=market_id
            )
            
            logger.info("Signing order...")
            signed_order = self.client.create_order(order_args)
            
            logger.info("Posting order...")
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

    def get_positions(self) -> List[Position]:
        """Fetch positions owned by the wallet using subgraph"""
        try:
            transport = RequestsHTTPTransport(
                url='https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/positions-subgraph/0.0.7/gn'
            )
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
                "address": self.wallet_address.lower()
            })
            
            positions = []
            for balance in result['userBalances']:
                if int(balance['balance']) > 0:
                    market_info = self.get_market(balance['asset']['id'])
                    prices = self.get_orderbook_price(balance['asset']['id'])
                    
                    positions.append(Position(
                        market_id=str(market_info["id"]),  # Convert to string
                        token_id=balance['asset']['id'],
                        market_question=market_info["question"],
                        outcomes=ast.literal_eval(market_info["outcomes"]),
                        prices=[float(p) for p in ast.literal_eval(market_info["outcome_prices"])],
                        balances=[int(balance['balance']) / 1e18 if i == int(balance['asset']['outcomeIndex']) else 0.0 
                                for i in range(len(ast.literal_eval(market_info["outcomes"])))]
                    ))
                    logger.info(f"Found position: {positions[-1]}")
                    
            return positions
        except Exception as e:
            logger.error(f"Error fetching positions from subgraph: {str(e)}")
            raise ValueError(f"Failed to fetch positions: {str(e)}")

# FastAPI setup
app = FastAPI(title="Polymarket Trading Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

trader = ServerTrader()

@app.get("/healthcheck")
async def healthcheck():
    return {"status": "ok"}

@app.get("/api/status")
async def get_status():
    try:
        balance = trader.usdc.functions.balanceOf(trader.wallet_address).call()
        balance_usdc = balance / 1e6
        
        allowance = trader.usdc.functions.allowance(trader.wallet_address, trader.exchange_address).call()
        allowance_usdc = allowance / 1e6
        
        markets = trader.client.get_sampling_simplified_markets()
        
        return {
            "status": "healthy",
            "wallet_address": trader.wallet_address,
            "balance_usdc": balance_usdc,
            "allowance_usdc": allowance_usdc,
            "markets_available": len(markets.get("data", [])) if markets else 0,
            "polygon_connected": trader.w3.is_connected()
        }
    except Exception as e:
        logger.error(f"Status check failed: {str(e)}")
        return {"status": "error", "error": str(e)}

@app.get("/api/positions")
async def get_positions():
    """Get all current positions and their values"""
    try:
        positions = trader.get_positions()
        formatted_positions = [{
            **position.dict(),
            "realized_pnl": float(position.realized_pnl) if hasattr(position, 'realized_pnl') else None,
            "unrealized_pnl": float(position.unrealized_pnl) if hasattr(position, 'unrealized_pnl') else None
        } for position in positions]
        return JSONResponse(content=formatted_positions)
    except Exception as e:
        logger.error(f"Failed to get positions: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

@app.post("/api/order")
async def place_order(order: OrderRequest):
    try:
        result = trader.execute_trade(
            market_id=order.market_id,
            price=order.price,
            amount=order.amount,
            side=order.side
        )
        return JSONResponse(content=result)
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": str(e), "type": "validation_error"}
        )
    except Exception as e:
        logger.error(f"Order execution failed: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

@app.post("/api/sell-position")
async def sell_position(position: SellPositionRequest):
    """Sell a position at market price (best available bid)"""
    try:
        # Get current position details
        positions = trader.get_positions()
        position_to_sell = next(
            (p for p in positions if p.token_id == position.token_id), 
            None
        )
        
        if not position_to_sell:
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": f"Position with token ID {position.token_id} not found"}
            )
            
        # Get current market price from orderbook
        orderbook = trader.client.get_order_book(position.token_id)
        if not orderbook.bids:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "No bids available in orderbook"}
            )
        
        price = float(orderbook.bids[0].price)
            
        # Verify we have enough balance
        available_balance = sum(position_to_sell.balances)
        if position.amount > available_balance:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False, 
                    "error": f"Insufficient balance. Have: {available_balance}, Want to sell: {position.amount}"
                }
            )
            
        # Execute the market sell
        result = trader.execute_trade(
            market_id=position.token_id,
            price=price,
            amount=position.amount,
            side="SELL"
        )
        
        return JSONResponse(content=result)
        
    except Exception as e:
        logger.error(f"Error selling position: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)