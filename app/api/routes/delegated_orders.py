from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from ...services.trader_service import TraderService
from ...services.postgres_service import PostgresService
from ...services.web3_service import Web3Service
from ...services.position_sync_service import PositionSyncService
from ...models.api import OrderRequest, OrderStatus
from ...config import logger

router = APIRouter()
trader_service = TraderService()
postgres_service = PostgresService()
web3_service = Web3Service()
position_sync_service = PositionSyncService(postgres_service)

@router.post("/api/delegated-order")
async def submit_delegated_order(order: OrderRequest):
    try:
        logger.info(f"Received delegated order request: {order.dict()}")
        
        # Convert the incoming raw amount to decimal USDC
        decimal_amount = float(order.amount) / 1_000_000
        
        logger.info(f"""
        Order Processing:
        Raw USDC amount: {order.amount}
        Decimal USDC amount: {decimal_amount}
        Price: {order.price}
        """)

        # Execute trade with the exact amount received
        try:
            logger.info(f"Executing trade for user: {order.user_address}")
            result = trader_service.execute_trade(
                token_id=order.token_id,
                price=order.price,
                amount=decimal_amount,
                side=order.side,
                is_yes_token=order.is_yes_token
            )
            
            if not result or not result.get('success'):
                raise ValueError("Trade execution failed")
                
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
    """
    Get user orders and positions, ensuring markets are synced to the database.
    
    Args:
        address: User's blockchain address
        
    Returns:
        Dict containing pending orders and completed positions
    """
    try:
        # Get pending orders (synchronous)
        pending_orders = postgres_service.get_user_pending_orders(address)
        
        # Get completed positions (async)
        completed_orders = await trader_service.get_positions()
        
        # Sync markets from positions
        await position_sync_service.sync_position_markets(completed_orders)
        
        return {
            "pending_orders": pending_orders,
            "completed_orders": completed_orders
        }
        
    except Exception as e:
        logger.error(f"Error getting user orders: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/order-status/{order_id}")
async def get_order_status(order_id: str):
    try:
        logger.info(f"Checking status for order: {order_id}")
        order = postgres_service.get_order(order_id)
        
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
        
        # Calculate price impact
        impact_analysis = trader_service.calculate_price_impact(
            order.token_id,
            float(order.amount) / 1_000_000,  # Convert to decimal
            order.price,
            order.side
        )
        
        # Log orderbook state and validation results
        logger.info(f"""
            Order Validation:
            - Side: {order.side}
            - Price: {order.price}
            - USDC Amount: {float(order.amount) / 1_000_000}
            - Best Bid: {trader_service.get_orderbook_price(order.token_id)[0]}
            - Best Ask: {trader_service.get_orderbook_price(order.token_id)[1]}
            """)

        # Return format that maintains backward compatibility
        return {
            "valid": True,
            "min_order_size": 1000000,  # 1 USDC in base units
            "max_order_size": 1000000000000,  # 1M USDC in base units
            "estimated_total": int(impact_analysis['estimated_total'] * 1_000_000),  # Convert back to base units
            "price_impact": impact_analysis['price_impact'],
            "execution_possible": impact_analysis['execution_possible'],
            "warning": None if impact_analysis['price_impact'] < 0.05 else 
                      f"High price impact: {impact_analysis['price_impact']*100:.1f}%"
        }

    except Exception as e:
        logger.error(f"Order validation failed: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))