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

@router.post("/api/delegated-order")
async def submit_delegated_order(order: OrderRequest):
    try:
        logger.info(f"Received delegated order request: {order.dict()}")
        
        # Validate order side
        if order.side not in ["BUY", "SELL"]:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid order side: {order.side}. Must be either 'BUY' or 'SELL'"
            )

        # Verify the token exists and is valid
        try:
            orderbook = trader_service.client.get_order_book(order.token_id)
            if not orderbook:
                raise HTTPException(status_code=400, detail="Invalid token ID")
        except Exception as e:
            logger.error(f"Token validation failed: {str(e)}")
            raise HTTPException(status_code=400, detail="Invalid token ID")

        # Execute trade - Remove the USDC unit conversion since amount is already correct
        try:
            logger.info(f"Executing trade for user: {order.user_address}")
            result = trader_service.execute_trade(
                token_id=order.token_id,
                price=order.price,
                amount=float(order.amount),  # Remove the division by 1_000_000
                side=order.side,
                is_yes_token=order.is_yes_token
            )

            logger.info(f"Trade execution result: {result}")
            return JSONResponse(content={
                "success": True,
                "status": "completed",
                "transaction": result
            })
        except Exception as e:
            logger.error(f"Trade execution failed: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Order submission failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    

@router.get("/api/user-orders/{address}")
async def get_user_orders(address: str):
    try:
        logger.info(f"Fetching orders for address: {address}")
        
        # Get pending orders from Redis
        pending_orders = redis_service.get_user_pending_orders(address)
        logger.info(f"Found {len(pending_orders)} pending orders")

        # Get completed orders from subgraph
        completed_orders = await trader_service.get_positions()
        logger.info(f"Found {len(completed_orders)} completed orders")

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
        logger.info(f"Checking status for order: {order_id}")
        order = redis_service.get_order(order_id)
        
        if not order:
            logger.warning(f"Order not found: {order_id}")
            raise HTTPException(status_code=404, detail="Order not found")

        logger.info(f"Order status: {order}")
        return OrderStatus(
            order_id=order_id,
            status=order['status'],
            error=order.get('error'),
            transaction_hash=order.get('transaction_hash')
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Failed to get order status: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/validate-order")
async def validate_order(order: OrderRequest):
    try:
        logger.info(f"Validating order request: {order.dict()}")
        
        if order.side not in ["BUY", "SELL"]:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid order side: {order.side}. Must be either 'BUY' or 'SELL'"
            )

        # Price is already in decimal format (e.g., 0.999)
        price = float(order.price)
        amount = float(order.amount) / 1_000_000  # Convert amount from USDC units

        try:
            orderbook = trader_service.client.get_order_book(order.token_id)
            if not orderbook:
                raise HTTPException(status_code=400, detail="Invalid token ID")
        except Exception as e:
            logger.error(f"Token validation failed: {str(e)}")
            raise HTTPException(status_code=400, detail="Invalid token ID")

        # Calculate expected USDC amount
        usdc_amount = price * amount * 1.02  # Including 2% buffer

        # Log prices for debugging
        logger.info(f"Input price (decimal): {price}")
        logger.info(f"Current orderbook: Bids={[b.price for b in orderbook.bids[:1] if orderbook.bids]}, Asks={[a.price for a in orderbook.asks[:1] if orderbook.asks]}")

        try:
            price_check = trader_service.check_price(
                token_id=order.token_id,
                expected_price=price,  # Already in decimal format
                side=order.side,
                is_yes_token=order.is_yes_token
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        return JSONResponse(content={
            "valid": True,
            "usdc_amount": str(int(usdc_amount * 1_000_000)),  # Convert back to USDC units
            "market_info": {
                "current_bid": orderbook.bids[0].price if orderbook.bids else None,
                "current_ask": orderbook.asks[0].price if orderbook.asks else None
            }
        })

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Order validation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))