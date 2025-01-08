import asyncio
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from web3 import Web3
from decimal import Decimal

from ...services.across_service import AcrossService
from ...services.web3_service import Web3Service
from ...config import logger

router = APIRouter()
across_service = AcrossService()
web3_service = Web3Service()

class BridgeRequest(BaseModel):
    user_address: str = Field(..., description="User's address on Optimism (destination chain)")
    amount: int = Field(..., description="Amount in USDC base units (6 decimals)")

class QuoteRequest(BaseModel):
    amount: int = Field(..., description="Amount in USDC base units (6 decimals)")

@router.get("/api/bridge/quote/{amount}")
async def get_bridge_quote(amount: int):
    """
    Get a quote for bridging USDC from Polygon to Optimism.
    Amount should be in USDC base units (6 decimals).
    """
    try:
        logger.info(f"Requesting bridge quote for amount: {amount} USDC base units")
        
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Amount must be greater than 0")
            
        try:
            quote = await across_service.get_bridge_quote(amount)
            
            # Extract limits and convert to USDC for readability
            limits = quote.get('limits', {})
            
            return {
                "quote_timestamp": quote.get("timestamp"),
                "fees": {
                    "total": {
                        "base_units": int(quote["totalRelayFee"]["total"]),
                        "usdc": float(quote["totalRelayFee"]["total"]) / 1_000_000,
                        "percentage": float(quote["totalRelayFee"]["pct"]) / 1e16  # Convert to percentage
                    },
                    "breakdown": {
                        "capital_fee": {
                            "base_units": int(quote["relayerCapitalFee"]["total"]),
                            "usdc": float(quote["relayerCapitalFee"]["total"]) / 1_000_000
                        },
                        "gas_fee": {
                            "base_units": int(quote["relayerGasFee"]["total"]),
                            "usdc": float(quote["relayerGasFee"]["total"]) / 1_000_000
                        },
                        "lp_fee": {
                            "base_units": int(quote["lpFee"]["total"]),
                            "usdc": float(quote["lpFee"]["total"]) / 1_000_000
                        }
                    }
                },
                "amounts": {
                    "input": {
                        "base_units": amount,
                        "usdc": amount / 1_000_000
                    },
                    "output": {
                        "base_units": amount - int(quote["totalRelayFee"]["total"]),
                        "usdc": (amount - int(quote["totalRelayFee"]["total"])) / 1_000_000
                    }
                },
                "timing": {
                    "estimated_seconds": quote.get("estimatedFillTimeSec", 900),
                    "is_instant": quote.get("estimatedFillTimeSec", 900) < 60
                },
                "limits": {
                    "min_deposit": float(limits.get("minDeposit", 0)) / 1_000_000,
                    "max_deposit": float(limits.get("maxDeposit", 0)) / 1_000_000,
                    "max_instant": float(limits.get("maxDepositInstant", 0)) / 1_000_000,
                    "input_within_limits": (
                        amount >= float(limits.get("minDeposit", 0)) and 
                        amount <= float(limits.get("maxDeposit", 0))
                    )
                },
                "contract_info": {
                    "spoke_pool": quote.get("spokePoolAddress"),
                    "destination_spoke_pool": quote.get("destinationSpokePoolAddress")
                },
                "is_amount_too_low": quote.get("isAmountTooLow", False)
            }
            
        except Exception as e:
            logger.error(f"Failed to get bridge quote: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
            
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Quote request failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    

@router.post("/api/bridge/quote")
async def post_bridge_quote(request: QuoteRequest):
    """
    Get a quote for bridging USDC from Polygon to Optimism using POST.
    Amount should be in USDC base units (6 decimals).
    """
    return await _process_quote(request.amount)

async def _process_quote(amount: int):
    """
    Internal function to process quote requests.
    """
    try:
        logger.info(f"Requesting bridge quote for amount: {amount}")
        
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Amount must be greater than 0")
            
        try:
            quote = await across_service.get_bridge_quote(amount)
            
            return {
                "total_fee": quote["totalRelayFee"]["total"],
                "output_amount": amount - int(quote["totalRelayFee"]["total"]),
                "estimated_time_seconds": quote.get("estimateFillTimeSec", 900),
                "max_instant": quote.get("maxDepositInstant", 0),
                "max_slow": quote.get("maxDeposit", 0)
            }
            
        except Exception as e:
            logger.error(f"Failed to get bridge quote: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
            
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Quote request failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/bridge")
async def bridge_usdc(request: BridgeRequest):
    """
    Bridge USDC from Polygon to Optimism using Across Protocol.
    Amount should be provided in USDC base units (e.g. 1000000 for 1 USDC)
    """
    try:
        logger.info(f"Received bridge request: {request.dict()}")
        
        # Validate address
        try:
            checksummed_address = Web3.to_checksum_address(request.user_address)
        except Exception as e:
            logger.error(f"Invalid address format: {str(e)}")
            raise HTTPException(status_code=400, detail="Invalid Ethereum address format")

        # Validate amount
        if request.amount <= 0:
            raise HTTPException(status_code=400, detail="Amount must be greater than 0")
            
        decimal_amount = float(request.amount) / 1_000_000
        logger.info(f"""
        Bridge Processing:
        Raw USDC amount: {request.amount}
        Decimal USDC amount: {decimal_amount}
        Destination address: {checksummed_address}
        """)
        
        # Execute bridge operation
        try:
            result = await across_service.initiate_bridge(
                user_address=checksummed_address,
                amount=request.amount
            )
            
            if not result or not result.get('success'):
                raise ValueError("Bridge initiation failed")
                
            logger.info(f"Bridge initiation result: {result}")
            return JSONResponse(content={
                "success": True,
                "transaction_hash": result["transaction_hash"],
                "bridge_details": result["bridge_details"],
                "estimated_time_seconds": result["bridge_details"]["estimated_time"]
            })
            
        except Exception as e:
            logger.error(f"Bridge execution failed: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
            
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Bridge request failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
@router.get("/api/bridge/routes")
async def get_available_routes():
    """
    Debug endpoint to check available bridge routes
    Analyzes all possible USDC routes between chains
    """
    try:
        routes = await across_service._get_available_routes()
        
        # Filter for any USDC variants (both USDC and USDC.e)
        usdc_routes = [
            route for route in routes 
            if any(symbol in route.get("originTokenSymbol", "") for symbol in ["USDC", "USDC.e"]) and
            any(symbol in route.get("destinationTokenSymbol", "") for symbol in ["USDC", "USDC.e"])
        ]
        
        # Group routes by origin chain
        routes_by_origin = {}
        for route in usdc_routes:
            origin_chain = route["originChainId"]
            if origin_chain not in routes_by_origin:
                routes_by_origin[origin_chain] = []
            routes_by_origin[origin_chain].append(route)
        
        # Find all possible paths from Polygon (137) to Optimism (10)
        polygon_to_optimism_paths = []
        
        # Direct paths
        direct_paths = [
            route for route in usdc_routes 
            if route["originChainId"] == 137 and route["destinationChainId"] == 10
        ]
        if direct_paths:
            polygon_to_optimism_paths.extend(direct_paths)
            
        # Indirect paths through other chains
        for route in usdc_routes:
            if route["originChainId"] == 137:  # Routes starting from Polygon
                intermediate_chain = route["destinationChainId"]
                # Look for connecting routes from intermediate chain to Optimism
                for second_hop in usdc_routes:
                    if (second_hop["originChainId"] == intermediate_chain and 
                        second_hop["destinationChainId"] == 10):
                        polygon_to_optimism_paths.append({
                            "type": "multi_hop",
                            "first_hop": route,
                            "second_hop": second_hop,
                            "via_chain": intermediate_chain
                        })
        
        return {
            "polygon_to_optimism_routes": {
                "direct_paths": [p for p in polygon_to_optimism_paths if "type" not in p],
                "indirect_paths": [p for p in polygon_to_optimism_paths if "type" in p],
                "path_count": len(polygon_to_optimism_paths)
            },
            "available_chains": {
                "origins": list(routes_by_origin.keys()),
                "polygon_destinations": [
                    route["destinationChainId"] 
                    for route in routes_by_origin.get(137, [])
                ],
                "optimism_origins": [
                    route["originChainId"] 
                    for route in usdc_routes 
                    if route["destinationChainId"] == 10
                ]
            },
            "usdc_variants": {
                "polygon": list(set(
                    route["originToken"] 
                    for route in usdc_routes 
                    if route["originChainId"] == 137
                )),
                "optimism": list(set(
                    route["destinationToken"] 
                    for route in usdc_routes 
                    if route["destinationChainId"] == 10
                ))
            }
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch routes: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/bridge/test-bridge")
async def test_bridge_transfer(
    user_address: str = Body(..., description="User's address on Optimism"),
    amount: int = Body(..., description="Amount in USDC base units (6 decimals)")
):
    """
    Test endpoint for executing a bridge transfer.
    Only use small amounts (e.g., 1-10 USDC) for testing.
    """
    try:
        # Input validation
        if amount > 10_000_000:  # Max 10 USDC for testing
            raise HTTPException(
                status_code=400, 
                detail="Test amount too high. Please use less than 10 USDC for testing."
            )

        logger.info(f"""
        Starting bridge test:
        User Address: {user_address}
        Amount: {amount} ({amount/1_000_000} USDC)
        """)

        # Step 1: Get quote
        try:
            quote = await across_service.get_bridge_quote(amount)
            logger.info(f"Received quote: {quote}")

            # Validate quote
            if quote.get("isAmountTooLow", False):
                min_amount = int(quote.get("limits", {}).get("minDeposit", 0))
                raise HTTPException(
                    status_code=400,
                    detail=f"Amount too low. Minimum is {min_amount/1_000_000} USDC"
                )
        except Exception as e:
            logger.error(f"Quote failed: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to get quote: {str(e)}")

        # Step 2: Verify balance and allowance
        try:
            # USDC Contract check
            usdc_contract = web3_service.usdc
            balance = usdc_contract.functions.balanceOf(web3_service.wallet_address).call()
            allowance = usdc_contract.functions.allowance(
                web3_service.wallet_address,
                across_service.spoke_pool.address
            ).call()

            logger.info(f"""
            Balance check:
            USDC Balance: {balance/1_000_000} USDC
            Current Allowance: {allowance/1_000_000} USDC
            Required Amount: {amount/1_000_000} USDC
            """)

            if balance < amount:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient balance. Have {balance/1_000_000} USDC, need {amount/1_000_000} USDC"
                )
        except Exception as e:
            logger.error(f"Balance/allowance check failed: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

        # Step 3: Execute bridge
        try:
            bridge_result = await across_service.initiate_bridge(
                user_address=Web3.to_checksum_address(user_address),
                amount=amount
            )
            
            logger.info(f"Bridge initiated: {bridge_result}")
            return {
                "status": "success",
                "details": {
                    "quote": {
                        "fee": int(quote["totalRelayFee"]["total"])/1_000_000,
                        "output_amount": (amount - int(quote["totalRelayFee"]["total"]))/1_000_000,
                    },
                    "transaction": bridge_result,
                    "estimated_time": quote.get("estimatedFillTimeSec", 900)
                }
            }
        except Exception as e:
            logger.error(f"Bridge execution failed: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Bridge execution failed: {str(e)}")

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Test bridge failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/swap/test-swap")
async def test_swap(
    amount: int = Body(..., description="Amount in USDC.e base units (6 decimals)"),
    slippage: float = Body(0.5, description="Slippage tolerance in percent")
):
    """
    Test endpoint for swapping USDC.e to USDC.
    Only use small amounts (e.g., 1-10 USDC) for testing.
    """
    try:
        # Input validation
        if amount > 10_000_000:  # Max 10 USDC for testing
            raise HTTPException(
                status_code=400, 
                detail="Test amount too high. Please use less than 10 USDC.e for testing."
            )

        if slippage < 0 or slippage > 5:
            raise HTTPException(
                status_code=400,
                detail="Slippage must be between 0 and 5 percent"
            )

        logger.info(f"""
        Starting swap test:
        Amount: {amount} ({amount/1_000_000} USDC.e)
        Slippage: {slippage}%
        """)

        # Check USDC.e balance first
        usdc_e_balance = web3_service.usdc.functions.balanceOf(web3_service.wallet_address).call()
        if usdc_e_balance < amount:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient USDC.e balance. Have: {usdc_e_balance/1_000_000}, Need: {amount/1_000_000}"
            )

        # Execute swap
        try:
            swap_result = await web3_service.swap_usdc_variants(amount, slippage)
            
            return {
                "status": "success",
                "details": {
                    "input_amount": amount/1_000_000,
                    "expected_output": swap_result['expected_output']/1_000_000,
                    "transaction_hash": swap_result['transaction_hash'],
                    "gas_used": swap_result['gas_used']
                }
            }
        except Exception as e:
            logger.error(f"Swap execution failed: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Test swap failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))