from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from ...services.trader_service import TraderService
from ...services.postgres_service import PostgresService
from ...services.web3_service import Web3Service
from ...models.api import OrderRequest, OrderStatus
from ...config import logger

router = APIRouter()
trader_service = TraderService()
postgres_service = PostgresService()
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
async def get_user_orders(address: str):  # Make route async
    try:
        # This stays synchronous
        pending_orders = postgres_service.get_user_pending_orders(address)
        # This needs await since it's async
        completed_orders = await trader_service.get_positions()
        return {"pending_orders": pending_orders, "completed_orders": completed_orders}
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
        
        # 1. Basic validation
        if order.side not in ["BUY", "SELL"]:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid order side: {order.side}. Must be either 'BUY' or 'SELL'"
            )

        # 2. Price and amount conversion
        price = float(order.price)
        usdc_decimal = float(order.amount) / 1_000_000  # Convert USDC units to decimal
        
        # 3. Get orderbook and validate token
        try:
            orderbook = trader_service.client.get_order_book(order.token_id)
            if not orderbook:
                raise HTTPException(status_code=400, detail="Invalid token ID")
                
            # Convert orderbook prices to floats for easier comparison
            bids = [(float(b.price), float(b.size)) for b in orderbook.bids] if orderbook.bids else []
            asks = [(float(a.price), float(a.size)) for a in orderbook.asks] if orderbook.asks else []
            
            best_bid = max(bid[0] for bid in bids) if bids else None
            best_ask = min(ask[0] for ask in asks) if asks else None
            
            logger.info(f"""
            Order Validation:
            - Side: {order.side}
            - Price: {price}
            - USDC Amount: {usdc_decimal}
            - Best Bid: {best_bid}
            - Best Ask: {best_ask}
            """)
            
        except Exception as e:
            logger.error(f"Token validation failed: {str(e)}")
            raise HTTPException(status_code=400, detail="Invalid token ID")

        # 4. Calculate required amounts
        # Use same fee buffer logic as execution
        if price <= 0.1:
            fee_buffer = 1.15  # 15% for very low prices
        elif price <= 0.5:
            fee_buffer = 1.08  # 8% for medium-low prices
        elif price <= 0.9:
            fee_buffer = 1.05  # 5% for medium-high prices
        else:
            fee_buffer = 1.02  # 2% for high prices

        # Calculate outcome tokens needed
        outcome_tokens = float(usdc_decimal / price)
        
        # 5. Check liquidity
        if order.side == "BUY":
            available_liquidity = sum(size for p, size in asks if p <= price)
            if outcome_tokens > available_liquidity:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Insufficient liquidity. Order requires {outcome_tokens:.2f} tokens but only {available_liquidity:.2f} available at price {price}"
                )
        else:  # SELL
            available_liquidity = sum(size for p, size in bids if p >= price)
            if outcome_tokens > available_liquidity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient liquidity. Order requires {outcome_tokens:.2f} tokens but only {available_liquidity:.2f} available at price {price}"
                )

        # 6. Price validation
        try:
            price_check = trader_service.check_price(
                token_id=order.token_id,
                expected_price=price,
                side=order.side,
                is_yes_token=order.is_yes_token
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        # 7. Calculate final amounts including buffers
        price_factor = 1 + ((1 - price) * 0.5)  # Same as execution
        base_usdc_needed = int(outcome_tokens * price * 1_000_000)  # Convert back to USDC units
        total_usdc_needed = int(base_usdc_needed * fee_buffer * price_factor)

        return JSONResponse(content={
            "valid": True,
            "usdc_amount": str(total_usdc_needed),  # Already in USDC units
            "market_info": {
                "current_bid": str(best_bid) if best_bid else None,
                "current_ask": str(best_ask) if best_ask else None,
                "available_liquidity": float(available_liquidity),
                "required_tokens": float(outcome_tokens),
                "fee_buffer": fee_buffer,
                "price_factor": price_factor
            }
        })

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Order validation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))