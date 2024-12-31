import asyncio
from decimal import Decimal
from typing import Optional, Dict, Any, Tuple
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds,
    OrderArgs,
    OrderType,
    MarketOrderArgs,
    BalanceAllowanceParams,
    AssetType,
    RequestArgs
)
from py_clob_client.endpoints import POST_ORDER
from py_clob_client.http_helpers.helpers import post
from py_clob_client.utilities import order_to_json
from py_clob_client.headers.headers import create_level_2_headers
import time
from ..config import PRIVATE_KEY, logger, CHAIN_ID

class CLOBService:
    """
    Centralized service for managing CLOB operations.
    Simplified to match working implementation pattern.
    """
    _instance = None
    _TOKEN_DECIMALS = 6
    _CLOB_HOST = "https://clob.polymarket.com"

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CLOBService, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize CLOB client with proper credentials."""
        if self._initialized:
            return
            
        try:
            self._initialize_client()
        except Exception as e:
            logger.error(f"Failed to initialize CLOB service: {str(e)}", exc_info=True)
            raise

    def _initialize_client(self):
        """Initialize single CLOB client with both L1 and L2 auth."""
        logger.info("Initializing CLOB client...")
        
        # Initialize base client
        self.client = ClobClient(
            self._CLOB_HOST,
            key=PRIVATE_KEY,
            chain_id=CHAIN_ID,
            signature_type=0
        )
        
        # Get and set credentials
        self.credentials = self.client.create_or_derive_api_creds()
        self.client.set_api_creds(self.credentials)
        
        logger.info("CLOB client initialized successfully")
        self._initialized = True

    def _get_api_credentials(self) -> Optional[ApiCreds]:
        """Get API credentials from L1 client"""
        for attempt in range(self._MAX_RETRIES):
            try:
                # Try to derive first
                try:
                    return self.l1_client.derive_api_key()
                except Exception:
                    # If derive fails, create new
                    return self.l1_client.create_api_key()
            except Exception as e:
                logger.error(f"API credentials attempt {attempt + 1} failed: {str(e)}")
                if attempt < self._MAX_RETRIES - 1:
                    time.sleep(self._RETRY_DELAY)
        return None
    
    async def post_order_with_confirmation(self, signed_order: Dict, order_type: OrderType, expected_fill_amount: float) -> Dict:
        """Post order using L2 auth and wait for confirmation"""
        try:
            # Use L2 client to post order
            body = order_to_json(signed_order, self.l2_client.creds.api_key, order_type)
            request_args = RequestArgs(
                method="POST",
                request_path=POST_ORDER,
                body=body
            )
            
            headers = create_level_2_headers(
                self.l2_client.signer,
                self.l2_client.creds,
                request_args
            )
            
            response = await self._submit_order(body, headers)
            
            if response.get("errorMsg"):
                raise ValueError(f"Order placement failed: {response['errorMsg']}")
                
            order_id = response.get("orderID")
            if not order_id:
                raise ValueError("No order ID returned from CLOB")
                
            # Wait for fill confirmation
            fill_info = await self._wait_for_order_fill(order_id, expected_fill_amount)
            
            return {
                "order_response": response,
                "fill_info": fill_info,
                "status": "filled",
                "transaction_hash": fill_info.get("transaction_hash")
            }
            
        except Exception as e:
            logger.error(f"Order posting failed: {str(e)}")
            raise


    def _verify_l2_auth(self):
        """Verify L2 authentication is working"""
        try:
            # Try to get API keys as a test
            self.l2_client.get_api_keys()
            logger.info("L2 authentication verified successfully")
        except Exception as e:
            logger.error(f"L2 authentication verification failed: {str(e)}")
            raise ValueError("Failed to verify L2 authentication")
        
    def _convert_balance_to_decimal(self, raw_balance: int) -> Tuple[Decimal, Decimal]:
        """
        Convert raw balance to both decimal and base units
        Returns: (decimal_balance, raw_balance_decimal)
        """
        raw_balance_decimal = Decimal(str(raw_balance))
        decimal_balance = raw_balance_decimal / Decimal(str(10 ** self._TOKEN_DECIMALS))
        return decimal_balance, raw_balance_decimal

    async def get_balance(self, token_id: str, signature_type: int = 0) -> Dict[str, Any]:
        """
        Get balance for a token with proper decimal conversion
        """
        try:
            balance_params = BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL,
                token_id=token_id,
                signature_type=signature_type
            )
            
            # Try to get current balance
            balance_info = self.client.get_balance_allowance(balance_params)
            
            # If balance check fails, try updating balance first
            if not balance_info or 'balance' not in balance_info:
                logger.info("Initial balance check failed, attempting balance update...")
                await self.update_balance_allowance(token_id, signature_type)
                balance_info = self.client.get_balance_allowance(balance_params)

            if not balance_info or 'balance' not in balance_info:
                raise ValueError("Failed to retrieve valid balance information")

            # Convert balance with proper decimals
            raw_balance = int(balance_info['balance'])
            decimal_balance, raw_balance_decimal = self._convert_balance_to_decimal(raw_balance)

            logger.info(f"""
            Balance Information:
            Raw Balance: {raw_balance}
            Decimal Balance: {decimal_balance}
            Base Units: {raw_balance_decimal}
            Token Decimals: {self._TOKEN_DECIMALS}
            """)

            return {
                **balance_info,
                'decimal_balance': decimal_balance,
                'raw_balance_decimal': raw_balance_decimal
            }
            
        except Exception as e:
            logger.error(f"Balance check failed: {str(e)}")
            raise

    async def update_balance_allowance(self, token_id: str, signature_type: int = 0) -> Dict[str, Any]:
        """
        Update balance allowance for a token.
        Note: A successful update returns an empty response from the CLOB API.
        """
        try:
            logger.info(f"""
            Initiating balance update:
            Token ID: {token_id}
            Signature Type: {signature_type}
            Using Client Address: {self.get_address()}
            """)
            
            balance_params = BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL,
                token_id=token_id,
                signature_type=signature_type
            )
            
            # Get initial balance for comparison
            initial_balance = self.get_balance_allowance(balance_params)
            logger.info(f"Initial balance state: {initial_balance}")
            
            # Perform update - note that a successful update returns empty response
            self.update_balance_allowance(balance_params)
            
            # Wait a moment for the update to propagate
            await asyncio.sleep(1)
            
            # Verify balance state after update
            updated_balance = self.get_balance_allowance(balance_params)
            logger.info(f"Updated balance state: {updated_balance}")
            
            return {
                'success': True,
                'initial_balance': initial_balance,
                'updated_balance': updated_balance
            }
                
        except Exception as e:
            logger.error(f"Balance update failed with error: {str(e)}")
            raise

    def get_order_book(self, token_id: str):
        """Get orderbook for a token."""
        try:
            return self.l2_client.get_order_book(token_id)
        except Exception as e:
            logger.error(f"Failed to get order book: {str(e)}")
            raise


    async def _submit_order(self, body: Dict, headers: Dict) -> Dict:
        """Submit order to CLOB with retry logic"""
        for attempt in range(self._MAX_RETRIES):
            try:
                response = post(f"{self._CLOB_HOST}{POST_ORDER}", headers=headers, data=body)
                return response
            except Exception as e:
                if attempt == self._MAX_RETRIES - 1:
                    raise
                logger.warning(f"Order submission attempt {attempt + 1} failed: {str(e)}")
                await asyncio.sleep(self._RETRY_DELAY)

    async def _wait_for_order_fill(self, order_id: str, expected_fill_amount: Optional[float] = None) -> Dict:
        """Wait for and verify order fill status."""
        start_time = time.time()
        
        while time.time() - start_time < self._MAX_ORDER_WAIT_TIME:
            try:
                # Get order status
                order_info = self.l2_client.get_order(order_id)
                
                if not order_info:
                    logger.warning(f"No order info returned for order {order_id}")
                    await asyncio.sleep(self._ORDER_CHECK_INTERVAL)
                    continue
                
                status = order_info.get("status", "unknown")
                logger.info(f"Order {order_id} status: {status}")
                
                if status == "filled":
                    return await self._verify_fill_amount(order_info, expected_fill_amount)
                elif status in ["cancelled", "expired", "rejected"]:
                    raise ValueError(f"Order {order_id} {status}")
                    
                await asyncio.sleep(self._ORDER_CHECK_INTERVAL)
                
            except Exception as e:
                logger.error(f"Error checking order status: {str(e)}")
                raise
                
        raise ValueError(f"Order {order_id} did not fill within {self._MAX_ORDER_WAIT_TIME} seconds")

    async def _verify_fill_amount(self, order_info: Dict, expected_fill_amount: Optional[float]) -> Dict:
        """Verify fill amount matches expectations"""
        fill_amount = float(order_info.get("filled_amount", 0))
        
        if expected_fill_amount is not None:
            if abs(fill_amount - expected_fill_amount) > 0.0001:
                raise ValueError(
                    f"Fill amount mismatch. Expected: {expected_fill_amount}, "
                    f"Got: {fill_amount}"
                )
        
        logger.info(f"Order {order_info.get('orderID')} filled successfully. Amount: {fill_amount}")
        return {
            "status": "filled",
            "fill_amount": fill_amount,
            "transaction_hash": order_info.get("transaction_hash"),
            "filled_at": order_info.get("filled_at")
        }

    async def cancel_order_if_unfilled(self, order_id: str):
        """
        Cancel an order if it hasn't been filled.
        """
        try:
            order_info = self.l2_client.get_order(order_id)
            if order_info.get("status") != "filled":
                logger.info(f"Cancelling unfilled order {order_id}")
                return self.l2_client.cancel(order_id)
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {str(e)}")

    def create_order(self, order_args: OrderArgs, options: Optional[Dict] = None):
        """Create a signed order using L2 client with optional delegation."""
        try:
            return self.l2_client.create_order(order_args, options)
        except Exception as e:
            logger.error(f"Failed to create order: {str(e)}")
            raise

    def post_order(self, signed_order: Dict, order_type: OrderType = OrderType.GTC):
        """Post an order using L2 client."""
        try:
            return self.l2_client.post_order(signed_order, order_type)
        except Exception as e:
            logger.error(f"Failed to post order: {str(e)}")
            raise