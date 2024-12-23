from fastapi import APIRouter
from fastapi.responses import JSONResponse
from ...services.trader_service import TraderService
from ...models.api import OrderRequest
from ...config import logger

router = APIRouter()
trader_service = TraderService()

@router.post("/api/order")
async def place_order(order: OrderRequest):
    try:
        result = trader_service.execute_trade(
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