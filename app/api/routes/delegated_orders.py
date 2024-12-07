from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from ...services.trader_service import TraderService
from ...services.redis_service import RedisService
from ...services.signature_service import SignatureService
from ...models import SignedOrder, OrderStatus
from ...config import logger

router = APIRouter()
trader_service = TraderService()
redis_service = RedisService()
signature_service = SignatureService(trader_service.web3_service.w3)

@router.post("/api/delegated-order")
async def submit_delegated_order(order: SignedOrder):
    try:
        # Verify nonce
        current_nonce = redis_service.get_user_nonce(order.user_address)
        if order.nonce <= current_nonce:
            raise HTTPException(status_code=400, detail="Invalid nonce")

        # Verify signature
        if not signature_service.verify_signature(order.dict(), order.signature):
            raise HTTPException(status_code=400, detail="Invalid signature")

        # Store pending order
        order_id = redis_service.store_pending_order(order.dict())

        # Execute trade
        try:
            result = trader_service.execute_trade(
                market_id=order.market_id,
                price=order.price,
                amount=order.amount,
                side=order.side
            )
            
            # Update order status
            redis_service.update_order_status(
                order_id=order_id,
                status="completed",
                tx_hash=result.get("order_id")
            )
            
            # Increment nonce after successful execution
            redis_service.increment_user_nonce(order.user_address)
            
            return JSONResponse(content={
                "success": True,
                "order_id": order_id,
                "status": "completed",
                "transaction": result
            })
            
        except Exception as e:
            redis_service.update_order_status(
                order_id=order_id,
                status="failed",
                error=str(e)
            )
            raise HTTPException(status_code=500, detail=str(e))
            
    except Exception as e:
        logger.error(f"Order submission failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/user-orders/{address}")
async def get_user_orders(address: str):
    try:
        # Get pending orders from Redis
        pending_orders = redis_service.get_user_pending_orders(address)
        
        # Get completed orders from subgraph (using existing positions query)
        completed_orders = await trader_service.get_positions()
        
        return JSONResponse(content={
            "pending_orders": pending_orders,
            "completed_orders": [order.dict() for order in completed_orders]
        })
        
    except Exception as e:
        logger.error(f"Failed to get user orders: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/order-status/{order_id}")
async def get_order_status(order_id: str):
    try:
        order = redis_service.get_order(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
            
        return OrderStatus(
            order_id=order_id,
            status=order['status'],
            error=order.get('error'),
            transaction_hash=order.get('transaction_hash')
        )
        
    except Exception as e:
        logger.error(f"Failed to get order status: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))