# app/api/routes/resolution.py
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from ...services.market_resolution import MarketResolutionService
from ...services.web3_service import Web3Service
from ...services.postgres_service import PostgresService
from ...config import logger

router = APIRouter()

# Initialize services - following your singleton pattern
web3_service = Web3Service()
postgres_service = PostgresService()
resolution_service = MarketResolutionService(web3_service, postgres_service)

@router.post("/api/resolve-markets")
async def resolve_markets():
    """
    Endpoint to trigger market resolution process.
    Checks for resolved markets and processes winning positions.
    """
    try:
        await resolution_service.process_unresolved_markets()
        return JSONResponse(
            content={
                "success": True,
                "message": "Market resolution process completed"
            }
        )
    except Exception as e:
        logger.error(f"Failed to process markets: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e)
            }
        )