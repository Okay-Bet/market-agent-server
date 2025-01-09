import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ...services.market_service import MarketService
from ...services.trader_service import TraderService
from ...services.postgres_service import PostgresService
from ...services.web3_service import Web3Service
from ...services.position_sync_service import PositionSyncService
from ...models.api import OrderRequest, OrderStatus
from ...config import logger

router = APIRouter()
market_service = MarketService()
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
        
        # First check all current approvals
        try:
            current_approvals = web3_service.check_all_approvals()
            logger.info(f"Current contract approvals: {current_approvals}")
            
            # If any required approvals are missing, approve all contracts
            needs_approval = any(
                not approval['ctf_approved'] or approval['usdc_allowance'] == 0
                for approval in current_approvals.values()
            )
            
            if needs_approval:
                logger.info("Missing approvals detected, initiating approval process...")
                approval_result = await web3_service.approve_all_contracts()
                if not approval_result['success']:
                    raise ValueError(f"Contract approval failed: {approval_result.get('error')}")
                logger.info("All contracts successfully approved")
            else:
                logger.info("All required approvals are already in place")
                
        except Exception as e:
            logger.error(f"Failed to handle approvals: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Approval process failed: {str(e)}")

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
    Get user orders and positions with enriched market data.
    """
    try:
        logger.info(f"Receiving request for user positions: {address}")
        
        # Get pending orders
        pending_orders = postgres_service.get_user_pending_orders(address)
        logger.info(f"Pending orders count: {len(pending_orders) if pending_orders else 0}")
        
        # Get positions
        positions = postgres_service.get_user_positions(address)
        logger.info(f"Raw positions from database: {positions}")
        
        market_service = MarketService()
        
        async def enrich_position(position):
            try:
                # Use token_id instead of condition_id for market data fetch
                if not position.get('token_id'):
                    logger.warning(f"Position missing token_id for condition {position['condition_id']}")
                    raise ValueError("Position missing token_id")
                
                # Fetch market data using token_id
                market_data = await market_service.get_market(position['token_id'])
                
                return {
                    "condition_id": position['condition_id'],
                    "token_id": position['token_id'],  # Include token_id in response
                    "balances": [float(position['amount']) * 1_000_000],
                    "prices": [float(position['entry_price'])],
                    "outcome": position['outcome'],
                    "status": position['status'].lower(),
                    "user_address": position['user_address'],
                    "market_data": {
                        "question": market_data["question"],
                        "outcomes": market_data["outcomes"],
                        "outcome_prices": market_data["outcome_prices"],
                    }
                }
            except ValueError as e:
                logger.warning(f"Could not fetch market data for position {position['condition_id']}: {str(e)}")
                # Return position without market data if fetch fails
                return {
                    "condition_id": position['condition_id'],
                    "token_id": position['token_id'],  # Include token_id even in error case
                    "balances": [float(position['amount']) * 1_000_000],
                    "prices": [float(position['entry_price'])],
                    "outcome": position['outcome'],
                    "status": position['status'].lower(),
                    "user_address": position['user_address'],
                }
        
        # Process all positions concurrently
        completed_orders = await asyncio.gather(
            *[enrich_position(position) for position in positions],
            return_exceptions=True
        )
        
        # Filter out any failed position enrichments
        completed_orders = [
            order for order in completed_orders
            if not isinstance(order, Exception)
        ]
        
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
        
        # Calculate minimum order size
        min_token_amount = 5
        min_usdc_amount = max(1.0, min_token_amount * order.price)
        min_order_size = int(min_usdc_amount * 1_000_000)
        max_order_size = 1_000_000_000_000

        logger.info(f"""
        Minimum Size Calculation:
        - Price: {order.price}
        - Min Tokens: {min_token_amount}
        - Min USDC: {min_usdc_amount}
        - Min Order Size (base units): {min_order_size}
        - Requested Amount: {order.amount}
        """)

        if int(order.amount) < min_order_size:
            return JSONResponse(
                status_code=400,
                content={
                    "valid": False,
                    "min_order_size": min_order_size,
                    "max_order_size": max_order_size,
                    "error": f"Order size ${float(order.amount)/1_000_000:.4f} below minimum ${min_usdc_amount:.4f}"
                }
            )

        # Calculate price impact
        impact_analysis = trader_service.calculate_price_impact(
            order.token_id,
            float(order.amount) / 1_000_000,
            order.price,
            order.side,
            order.is_yes_token  # Kept for API compatibility but not used
        )

        if not impact_analysis.get("valid", False):
            return JSONResponse(
                status_code=400,
                content={
                    "valid": False,
                    "min_order_size": min_order_size,
                    "max_order_size": max_order_size,
                    "error": impact_analysis.get("error", "Price impact calculation failed"),
                    "market_price": impact_analysis.get("market_price")
                }
            )

        price_impact = impact_analysis.get("price_impact", 0)
        return {
            "valid": True,
            "min_order_size": min_order_size,
            "max_order_size": max_order_size,
            "estimated_total": impact_analysis.get("estimated_total", int(order.amount)),
            "price_impact": price_impact,
            "execution_possible": impact_analysis.get("execution_possible", True),
            "warning": None if abs(price_impact) < 0.05 else f"High price impact: {price_impact*100:.1f}%",
            "market_price": impact_analysis.get("market_price")
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