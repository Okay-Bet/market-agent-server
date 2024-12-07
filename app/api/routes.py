# src/api/routes.py
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from ..models import OrderRequest, SellPositionRequest
from ..services.trader_service import TraderService
from ..config import logger

router = APIRouter()
trader_service = TraderService()

@router.get("/healthcheck")
async def healthcheck():
    return {"status": "ok"}

@router.get("/api/status")
async def get_status():
    try:
        balance = trader_service.web3_service.usdc.functions.balanceOf(
            trader_service.web3_service.wallet_address
        ).call()
        balance_usdc = balance / 1e6
        
        allowance = trader_service.web3_service.usdc.functions.allowance(
            trader_service.web3_service.wallet_address,
            trader_service.web3_service.w3.to_checksum_address(EXCHANGE_ADDRESS)
        ).call()
        allowance_usdc = allowance / 1e6
        
        markets = trader_service.client.get_sampling_simplified_markets()
        
        return {
            "status": "healthy",
            "wallet_address": trader_service.web3_service.wallet_address,
            "balance_usdc": balance_usdc,
            "allowance_usdc": allowance_usdc,
            "markets_available": len(markets.get("data", [])) if markets else 0,
            "polygon_connected": trader_service.web3_service.w3.is_connected()
        }
    except Exception as e:
        logger.error(f"Status check failed: {str(e)}")
        return {"status": "error", "error": str(e)}

@router.get("/api/positions")
async def get_positions():
    try:
        positions = await trader_service.get_positions()