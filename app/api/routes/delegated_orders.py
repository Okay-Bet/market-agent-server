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
                is_yes_token=order.is_yes_token,
                user_address=order.user_address
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
    Get user orders and positions, using our existing postgres service.
    """
    try:
        # Debug log for incoming request
        logger.info(f"Receiving request for user positions: {address}")

        # Get pending orders
        pending_orders = postgres_service.get_user_pending_orders(address)
        logger.info(f"Pending orders count: {len(pending_orders) if pending_orders else 0}")

        # Get positions
        positions = postgres_service.get_user_positions(address)
        logger.info(f"Raw positions from database: {positions}")

        # Transform positions into frontend format
        completed_orders = []
        for position in positions:
            position_dict = {
                "market_id": position['condition_id'],
                "balances": [float(position['amount']) * 1_000_000],
                "prices": [float(position['entry_price'])],
                "outcome": position['outcome'],
                "status": position['status'].lower(),
                "user_address": position['user_address']
            }
            completed_orders.append(position_dict)

        # Log the final response
        response_data = {
            "pending_orders": pending_orders,
            "completed_orders": completed_orders
        }
        logger.info(f"Sending response: {response_data}")
        
        return response_data

    except Exception as e:
        logger.error(f"Error processing user orders: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error processing user orders: {str(e)}"
        )


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
        
        # Calculate minimum order size based on price
        min_token_amount = 5  # Always need 5 tokens minimum
        min_usdc_amount = max(1.0, min_token_amount * order.price)  # Use $1 minimum if token amount would be less
        min_order_size = int(min_usdc_amount * 1_000_000)  # Convert to base units
        max_order_size = 1_000_000_000_000  # $1M in base units
        
        # Log minimum size calculation
        logger.info(f"""
            Minimum Size Calculation:
            - Price: {order.price}
            - Min Tokens: {min_token_amount}
            - Min USDC: {min_usdc_amount}
            - Min Order Size (base units): {min_order_size}
            - Requested Amount: {order.amount}
        """)

        # Validate minimum order size
        if int(order.amount) < min_order_size:
            return JSONResponse(
                status_code=400,
                content={
                    "valid": False,
                    "min_order_size": min_order_size,
                    "max_order_size": max_order_size,
                    "error": f"Order size ${float(order.amount)/1_000_000:.4f} below minimum ${min_usdc_amount:.4f} (5 tokens at price {order.price})"
                }
            )

        # Calculate price impact
        impact_analysis = trader_service.calculate_price_impact(
            order.token_id,
            float(order.amount) / 1_000_000,
            order.price,
            order.side
        )

        # Check if the impact analysis was successful
        if not impact_analysis.get("valid", False):
            return JSONResponse(
                status_code=400,
                content={
                    "valid": False,
                    "min_order_size": min_order_size,
                    "max_order_size": max_order_size,
                    "error": impact_analysis.get("error", "Price impact calculation failed")
                }
            )

        # If we got here, we have a valid impact analysis
        return {
            "valid": True,
            "min_order_size": min_order_size,
            "max_order_size": max_order_size,
            "estimated_total": impact_analysis.get("estimated_total", int(order.amount)),
            "price_impact": impact_analysis.get("price_impact", 0),
            "execution_possible": impact_analysis.get("execution_possible", True),
            "warning": None if impact_analysis.get("price_impact", 0) < 0.05 else 
                      f"High price impact: {impact_analysis['price_impact']*100:.1f}%"
        }

    except Exception as e:
        logger.error(f"Order validation failed: {str(e)}")
        return JSONResponse(
            status_code=400,
            content={
                "valid": False,
                "min_order_size": min_order_size if 'min_order_size' in locals() else 1_000_000,
                "max_order_size": max_order_size if 'max_order_size' in locals() else 1_000_000_000_000,
                "error": str(e)
            }
        )