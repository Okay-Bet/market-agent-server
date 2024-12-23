from fastapi import APIRouter
from fastapi.responses import JSONResponse
from ...services.trader_service import TraderService
from ...models.api import SellPositionRequest
from ...config import logger

router = APIRouter()
trader_service = TraderService()

@router.get("/api/positions")
async def get_positions():
    try:
        positions = await trader_service.get_positions()
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

@router.post("/api/sell-position")
async def sell_position(position: SellPositionRequest):
    try:
        positions = await trader_service.get_positions()
        position_to_sell = next(
            (p for p in positions if p.token_id == position.token_id), 
            None
        )
        
        if not position_to_sell:
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": f"Position with token ID {position.token_id} not found"}
            )
            
        orderbook = trader_service.client.get_order_book(position.token_id)
        if not orderbook.bids:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "No bids available in orderbook"}
            )
        
        price = float(orderbook.bids[0].price)
        available_balance = sum(position_to_sell.balances)
        
        if position.amount > available_balance:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False, 
                    "error": f"Insufficient balance. Have: {available_balance}, Want to sell: {position.amount}"
                }
            )
            
        result = trader_service.execute_trade(
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