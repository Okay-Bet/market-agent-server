import aiohttp
from web3 import Web3
from decimal import Decimal
import time
from typing import Dict, Any
from .web3_service import Web3Service
from ..config import logger, ACROSS_SPOKE_POOL_ABI, USDC_ABI

web3_service = Web3Service()

class AcrossService:
    def __init__(self):
        self.API_BASE_URL = "https://app.across.to/api"
        self.web3_service = Web3Service()
        
        # Correct token addresses from route discovery
        self.POLYGON_USDC = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"  # Polygon USDC
        self.OPTIMISM_USDC = "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85"  # Optimism USDC (native)
        self.POLYGON_CHAIN_ID = 137
        self.OPTIMISM_CHAIN_ID = 10

        # Initialize SpokePool
        self.spoke_pool_address = None
        self.spoke_pool = None

        # Initialize USDC contract specifically for bridging
        self.bridge_usdc = self.web3_service.w3.eth.contract(
            address=Web3.to_checksum_address(self.POLYGON_USDC),
            abi=USDC_ABI  # Using the same ABI since it's still USDC
        )

    async def _get_available_routes(self) -> list:
        """
        Fetch and cache available bridge routes from Across API
        """
        if self._available_routes is not None:
            return self._available_routes

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.API_BASE_URL}/available-routes") as response:
                if response.status != 200:
                    raise ValueError(f"Failed to fetch available routes: {await response.text()}")
                
                routes = await response.json()
                self._available_routes = routes
                return routes

    async def _validate_route(self) -> bool:
        """
        Validate if Polygon -> Optimism USDC route is available
        """
        routes = await self._get_available_routes()
        
        # Look for our specific route
        for route in routes:
            if (route.get("originChainId") == self.POLYGON_CHAIN_ID and 
                route.get("destinationChainId") == self.OPTIMISM_CHAIN_ID and 
                route.get("tokenAddress", "").lower() == self.POLYGON_USDC.lower()):
                return True
                
        return 
    
    async def _init_spoke_pool(self, address: str):
        """Initialize or update the SpokePool contract with a given address"""
        if self.spoke_pool is None or self.spoke_pool_address != address:
            self.spoke_pool_address = Web3.to_checksum_address(address)
            self.spoke_pool = self.web3_service.w3.eth.contract(
                address=self.spoke_pool_address,
                abi=ACROSS_SPOKE_POOL_ABI
            )
            logger.info(f"Initialized SpokePool contract at {self.spoke_pool_address}")

    async def get_bridge_quote(self, amount: int) -> Dict[str, Any]:
        """
        Get quote from Across API for bridging USDC from Polygon to Optimism
        
        Args:
            amount: Amount in USDC base units (6 decimals)
            
        Returns:
            Dict containing quote details including fees and timestamps
        """
        async with aiohttp.ClientSession() as session:
            params = {
                "token": self.POLYGON_USDC,
                "originChainId": self.POLYGON_CHAIN_ID,
                "destinationChainId": self.OPTIMISM_CHAIN_ID,
                "amount": str(amount)
            }
            
            logger.info(f"Requesting quote with params: {params}")
            
            async with session.get(f"{self.API_BASE_URL}/suggested-fees", params=params) as response:
                response_text = await response.text()
                
                if response.status != 200:
                    logger.error(f"Bridge quote failed with status {response.status}: {response_text}")
                    try:
                        error_json = await response.json()
                        error_message = error_json.get('message', response_text)
                    except:
                        error_message = response_text
                    raise ValueError(f"Failed to get bridge quote: {error_message}")
                
                try:
                    quote = await response.json()
                    logger.info(f"Received quote response: {quote}")
                    spoke_pool_address = quote.get('spokePoolAddress')
                    if not spoke_pool_address:
                        raise ValueError("Quote did not return spoke pool address")
                    await self._init_spoke_pool(spoke_pool_address)
                    return quote
                except Exception as e:
                    raise ValueError(f"Failed to parse quote response: {str(e)}")

    async def initiate_bridge(self, user_address: str, amount: int) -> dict:
        """Initiate bridge transfer using Across Protocol"""
        if not self.spoke_pool:
            raise ValueError("SpokePool contract not initialized. Get a quote first.")

        # Get quote and calculate parameters
        quote = await self.get_bridge_quote(amount)
        
        # Calculate output amount (input - fees)
        output_amount = amount - int(quote["totalRelayFee"]["total"])
        current_time = int(time.time())
        
        # Prepare deposit parameters
        deposit_params = {
            "spoke_pool_address": self.spoke_pool_address,
            "depositor": user_address,
            "recipient": user_address,
            "inputToken": self.POLYGON_USDC,  # Using the correct USDC for bridging
            "outputToken": self.OPTIMISM_USDC,
            "inputAmount": amount,
            "outputAmount": output_amount,
            "destinationChainId": self.OPTIMISM_CHAIN_ID,
            "exclusiveRelayer": quote.get("exclusiveRelayer", "0x0000000000000000000000000000000000000000"),
            "quoteTimestamp": quote["timestamp"],
            "fillDeadline": current_time + 18000,  # 5 hours
            "exclusivityDeadline": quote.get("exclusivityDeadline", 0),
            "message": "0x"
        }

        logger.info(f"Initiating bridge with params: {deposit_params}")
        
        # Execute the bridge transaction using the correct USDC contract
        result = await self.web3_service.send_across_deposit(
            deposit_params,
            token_contract=self.bridge_usdc  # Pass the correct USDC contract
        )
        
        return result