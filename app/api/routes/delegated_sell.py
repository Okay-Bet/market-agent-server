from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from ...services.trader_service import TraderService
from ...services.redis_service import RedisService
from ...services.web3_service import Web3Service
from ...models import OrderRequest, OrderStatus
from ...config import logger

router = APIRouter()
trader_service = TraderService()
redis_service = RedisService()
web3_service = Web3Service()

@router.post("/api/delegated-sell")
async def submit_delegated_sell(order: OrderRequest):
    """Route handler for delegated sell orders"""
    try:
        # Request validation
        if order.side != "SELL":
            raise HTTPException(
                status_code=422,
                detail="This endpoint is for sell orders only"
            )

        # Price validation
        try:
            price = float(order.price)
            if price <= 0 or price >= 1:
                raise ValueError
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="Invalid price. Must be between 0 and 1"
            )

        # Amount validation
        try:
            amount = int(order.amount)
            if amount <= 0:
                raise ValueError
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="Invalid amount. Must be positive integer"
            )

        # Execute trade via service
        try:
            result = await trader_service.execute_delegated_sell(
                token_id=order.token_id,
                price=price,
                amount=amount,
                is_yes_token=order.is_yes_token,
                user_address=order.user_address
            )
            return JSONResponse(content=result)
        except ValueError as e:
            # Convert known trading errors to appropriate HTTP responses
            error_msg = str(e).lower()
            if any(keyword in error_msg for keyword in ["insufficient", "liquidity", "balance"]):
                raise HTTPException(status_code=400, detail=str(e))
            else:
                raise HTTPException(status_code=500, detail=str(e))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in sell endpoint: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred processing your request"
        )