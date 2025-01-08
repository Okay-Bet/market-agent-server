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

class QuoteRequest(BaseModel):
    amount: int = Field(..., description="Amount in USDC.e base units (6 decimals)")

class SwapRequest(BaseModel):
    amount: int = Field(..., description="Amount in USDC.e base units (6 decimals)")
    slippage: float = Field(0.5, description="Slippage tolerance in percent (default: 0.5%)")
    route: str = Field("direct", description="Route to use: 'direct' or 'via_usdt'")

@router.post("/api/swap/quote")
async def get_swap_quote(request: QuoteRequest):
    """
    Get quotes for swapping USDC.e to USDC across different routes
    """
    try:
        logger.info(f"Requesting swap quote for amount: {request.amount}")
        
        if request.amount <= 0:
            raise HTTPException(status_code=400, detail="Amount must be greater than 0")
            
        try:
            quote = await web3_service.get_swap_quote(request.amount)
            
            # Add human-readable amounts
            for path_key, path_data in quote["quotes"].items():
                if "error" not in path_data:
                    path_data["amounts_readable"] = {
                        "input": path_data["input_amount"] / 1_000_000,
                        "output": path_data["output_amount"] / 1_000_000
                    }
            
            return JSONResponse(content={
                "quotes": quote["quotes"],
                "recommended_route": quote["best_route"],
                "input_amount": {
                    "base_units": request.amount,
                    "usdc": request.amount / 1_000_000
                },
                "recommended_slippage": quote["recommended_slippage"]
            })
            
        except Exception as e:
            logger.error(f"Failed to get swap quote: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
            
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Quote request failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/swap")
async def swap_usdc(request: SwapRequest):
    """
    Execute USDC.e to USDC swap using the best available route
    """
    try:
        logger.info(f"Received swap request for {request.amount} USDC.e")
        
        if request.amount <= 0:
            raise HTTPException(status_code=400, detail="Amount must be greater than 0")
            
        if request.slippage < 0 or request.slippage > 5:
            raise HTTPException(
                status_code=400, 
                detail="Slippage must be between 0 and 5 percent"
            )
            
        result = await web3_service.execute_swap(
            amount=request.amount,
            slippage_percent=request.slippage
        )
        
        return JSONResponse(content=result)
        
    except Exception as e:
        logger.error(f"Swap request failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))