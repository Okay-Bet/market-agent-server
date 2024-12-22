from fastapi import APIRouter
from fastapi.responses import JSONResponse
from ...services.trader_service import TraderService
from ...services.postgres_service import PostgresService
from ...models import OrderRequest
from ...config import logger

router = APIRouter()
trader_service = TraderService()
postgres_service = PostgresService()

@router.get("/api/debug/unresolved-markets")
async def get_unresolved_markets():
    """Debug endpoint to check unresolved markets in database"""
    try:
        markets = await postgres_service.get_unresolved_markets()
        return {
            "status": "success",
            "count": len(markets) if markets else 0,
            "markets": markets
        }
    except Exception as e:
        logger.error(f"Error getting unresolved markets: {str(e)}")
        return {"status": "error", "message": str(e)}
    
@router.get("/api/debug/market-details/{market_id}")
async def get_market_details(market_id: str):
    """Debug endpoint to get all market details"""
    try:
        # Get unresolved markets to check if this one is in the list
        unresolved_markets = await postgres_service.get_unresolved_markets()
        is_unresolved = any(m.get('market_id') == market_id for m in unresolved_markets)
        
        # Get all positions for this market
        positions = await postgres_service.get_market_positions(market_id)
        
        # Try to get condition ID from positions
        condition_id = None
        if positions and len(positions) > 0:
            condition_id = positions[0].get('condition_id')
        
        # If we have a condition ID, check on-chain status
        on_chain_status = None
        if condition_id:
            try:
                payout_denominator = await web3_service.ctf_contract.functions.payoutDenominator(condition_id).call()
                on_chain_status = {
                    "payout_denominator": payout_denominator,
                    "is_resolved": payout_denominator > 0
                }
            except Exception as e:
                on_chain_status = {"error": str(e)}
        
        return {
            "status": "success",
            "market_id": market_id,
            "is_in_unresolved_list": is_unresolved,
            "condition_id": condition_id,
            "position_count": len(positions),
            "positions": positions,
            "on_chain_status": on_chain_status
        }
    except Exception as e:
        logger.error(f"Error getting market details: {str(e)}")
        return {"status": "error", "message": str(e)}

@router.get("/api/debug/check-market/{condition_id}")
async def check_market_status(condition_id: str):
    """Debug endpoint to check market status on-chain"""
    try:
        # Check payout denominator
        payout_denominator = await web3_service.ctf_contract.functions.payoutDenominator(condition_id).call()
        
        # Get market data from database
        market = await postgres_service.get_market(condition_id)
        
        return {
            "status": "success",
            "on_chain": {
                "payout_denominator": payout_denominator,
                "is_resolved": payout_denominator > 0
            },
            "market_data": market
        }
    except Exception as e:
        logger.error(f"Error checking market status: {str(e)}")
        return {"status": "error", "message": str(e)}

@router.post("/api/debug/add-market")
async def add_test_market(
    market_id: str,
    condition_id: str,
    collateral_token: str
):
    """Debug endpoint to add a market to unresolved list"""
    try:
        await postgres_service.create_market({
            'market_id': market_id,
            'condition_id': condition_id,
            'collateral_token': collateral_token,
            'status': 'unresolved',
            'metadata': {
                'market_id': market_id,
                'collateral_token': collateral_token
            }
        })
        return {
            "status": "success",
            "message": f"Added market {market_id} with condition {condition_id} to unresolved list"
        }
    except Exception as e:
        logger.error(f"Error adding test market: {str(e)}")
        return {"status": "error", "message": str(e)}

@router.post("/api/debug/trigger-with-logs")
async def trigger_resolution_with_logs():
    """Debug endpoint to trigger resolution with enhanced logging"""
    if not market_resolution_service:
        return {"status": "error", "message": "Market resolution service not initialized"}
    
    try:
        # Get initial state
        markets = await postgres_service.get_unresolved_markets()
        initial_state = {
            "unresolved_markets_count": len(markets) if markets else 0,
            "markets": markets
        }
        
        # Trigger processing
        await market_resolution_service.process_unresolved_markets()
        
        # Get final state
        markets_after = await postgres_service.get_unresolved_markets()
        final_state = {
            "unresolved_markets_count": len(markets_after) if markets_after else 0,
            "markets": markets_after
        }
        
        return {
            "status": "success",
            "initial_state": initial_state,
            "final_state": final_state
        }
    except Exception as e:
        logger.error(f"Manual market resolution failed: {str(e)}")
        return {"status": "error", "message": str(e)}

# Optional: Add manual trigger endpoint for testing
@router.post("/api/debug/manual-market-resolution")
async def trigger_market_resolution():
    """Manual trigger endpoint for testing market resolution"""
    if not market_resolution_service:
        return {"status": "error", "message": "Market resolution service not initialized"}
    
    try:
        await market_resolution_service.process_unresolved_markets()
        return {
            "status": "success", 
            "message": "Market resolution process triggered"
        }
    except Exception as e:
        logger.error(f"Manual market resolution failed: {str(e)}")
        return {
            "status": "error", 
            "message": str(e)
        }