from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from ...services.trader_service import TraderService
from ...services.postgres_service import PostgresService
from ...services.web3_service import Web3Service
from ...models.api import OrderRequest
from ...config import logger

router = APIRouter()
trader_service = TraderService()
postgres_service = PostgresService()
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

        # Execute trade via service
        try:
            result = await trader_service.execute_delegated_sell(
                token_id=order.token_id,
                price=float(order.price),
                amount=int(order.amount),
                is_yes_token=order.is_yes_token,
                user_address=order.user_address  # Pass through the user_address
            )
            
            return JSONResponse(content=result)
        except ValueError as e:
            # Convert known trading errors to appropriate HTTP responses
            error_msg = str(e).lower()
            if "insufficient" in error_msg:
                raise HTTPException(status_code=400, detail=str(e))
            elif "liquidity" in error_msg:
                raise HTTPException(status_code=400, detail=str(e))
            elif "balance" in error_msg:
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

@router.post("/api/validate-sell")
async def validate_sell_order(order: OrderRequest):
    """
    Validates a sell order and determines if it needs to be split into smaller orders
    based on available liquidity.
    """
    try:
        logger.info(f"Validating sell order request: {order.dict()}")
        
        if order.side != "SELL":
            raise HTTPException(
                status_code=422,
                detail="This endpoint is for sell orders only"
            )

        # Convert amounts to proper units
        price = float(order.price)
        token_decimal = float(order.amount) / 1_000_000  # Convert to decimal tokens
        
        # Define thresholds
        DUST_THRESHOLD = 5.0  # Positions smaller than 5 tokens are considered dust
        MIN_ORDER_SIZE = 5.0   # Minimum executable order size
        MAX_ORDERS = 5         # Maximum number of split orders
        MIN_PRICE_IMPACT = 0.02  # 2% max price deviation for market orders
        
        # Check if position is too small to be worth selling
        if token_decimal < DUST_THRESHOLD:
            return JSONResponse(content={
                "valid": False,
                "status": "dust_position",
                "detail": f"Position size ({token_decimal:.4f} tokens) is too small to be worth selling"
            })

        # Get orderbook and validate token
        try:
            orderbook = trader_service.client.get_order_book(order.token_id)
            if not orderbook or not orderbook.bids:
                raise HTTPException(status_code=400, detail="No bids available in market")
            
            # Convert orderbook to floats and sort by price
            bids = sorted(
                [(float(b.price), float(b.size)) for b in orderbook.bids],
                key=lambda x: x[0],
                reverse=True  # Highest price first for bids
            )
            
            best_bid = bids[0][0]
            
            logger.info(f"""
            Order Details:
            - Side: SELL
            - Price: {price}
            - Token Amount: {token_decimal}
            - Best Bid: {best_bid}
            """)

            # Log all bids for debugging
            logger.info("Available bids:")
            for bid_price, bid_size in bids:
                logger.info(f"- Price: {bid_price}, Size: {bid_size}")
            
        except Exception as e:
            logger.error(f"Token/orderbook validation failed: {str(e)}")
            raise HTTPException(status_code=400, detail="Invalid token ID or orderbook")

        # Calculate minimum acceptable price with more flexible slippage for depth
        min_acceptable_price = min(
            price * 0.96,  # Allow up to 4% slippage for better liquidity
            best_bid * 0.99  # Stay close to best bid
        )
        logger.info(f"Minimum acceptable price: {min_acceptable_price}")

        # Calculate available liquidity at acceptable prices
        total_liquidity = 0
        weighted_avg_price = 0
        usable_bids = []

        for bid_price, bid_size in bids:
            if bid_price >= min_acceptable_price:
                total_liquidity += bid_size
                weighted_avg_price += (bid_price * bid_size)
                usable_bids.append({
                    "price": bid_price,
                    "size": bid_size
                })
                logger.info(f"Adding to liquidity: {bid_size} tokens at {bid_price}")

        if total_liquidity > 0:
            weighted_avg_price /= total_liquidity

            logger.info(f"""
            Liquidity Summary:
            - Total liquidity: {total_liquidity} tokens
            - Weighted avg price: {weighted_avg_price}
            - Original price: {price}
            - Best bid: {best_bid}
            - Min acceptable: {min_acceptable_price}
            - Number of usable bids: {len(usable_bids)}
            """)

        # Calculate split orders
        split_orders = []
        remaining_amount = min(token_decimal, total_liquidity)

        if total_liquidity >= MIN_ORDER_SIZE:
            # Create split orders
            for bid in usable_bids:
                if len(split_orders) >= MAX_ORDERS or remaining_amount < MIN_ORDER_SIZE:
                    break

                order_size = min(
                    remaining_amount,
                    bid["size"],
                    token_decimal / MAX_ORDERS  # Try to distribute evenly
                )

                if order_size >= MIN_ORDER_SIZE:
                    split_orders.append({
                        "price": str(bid["price"]),
                        "token_amount": str(order_size),
                        "usdc_amount": str(int(order_size * bid["price"] * 1_000_000))
                    })
                    remaining_amount -= order_size
                    logger.info(f"Created split order: {order_size} tokens at {bid['price']}")

        # Determine status
        if total_liquidity < MIN_ORDER_SIZE:
            status = "no_liquidity"
        elif total_liquidity < token_decimal:
            status = "partial_fill_possible"
        else:
            status = "full_fill_possible"

        response_data = {
            "valid": True,
            "status": status,
            "original_order": {
                "token_amount": str(token_decimal),
                "price": str(price),
                "usdc_amount": str(int(token_decimal * price * 1_000_000))
            },
            "market_info": {
                "best_bid": str(best_bid),
                "weighted_avg_price": str(weighted_avg_price),
                "available_liquidity": float(total_liquidity),
                "max_possible_amount": float(min(token_decimal, total_liquidity)),
                "fee_buffer": 1.02,
                "price_factor": 1.5,
                "is_market_order": False,
                "usable_bids": len(usable_bids)
            },
            "split_orders": split_orders if split_orders else None,
            "remaining_amount": float(remaining_amount) if remaining_amount > MIN_ORDER_SIZE else 0,
            "dust_threshold": DUST_THRESHOLD,
            "min_order_size": MIN_ORDER_SIZE
        }

        logger.info(f"Validation response: {response_data}")
        return JSONResponse(content=response_data)

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Sell order validation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))