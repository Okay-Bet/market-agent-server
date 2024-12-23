from redis import Redis
from typing import Optional, Dict, Any, List
import json
import hashlib
from ..config import logger

class RedisService:
    def __init__(self):
        self.redis = Redis(host='localhost', port=6379, db=0)
        
    def generate_order_id(self, user_address: str, nonce: int) -> str:
        """Generate a unique order ID based on user address and nonce"""
        return hashlib.sha256(f"{user_address}:{nonce}".encode()).hexdigest()

    def store_pending_order(self, order_data: Dict[str, Any]) -> str:
        """Store a pending order and return order ID"""
        order_id = self.generate_order_id(order_data['user_address'], order_data['nonce'])
        self.redis.hset(
            f"order:{order_id}",
            mapping={
                "user_address": order_data['user_address'],
                "market_id": order_data['market_id'],
                "price": str(order_data['price']),
                "amount": str(order_data['amount']),
                "side": order_data['side'],
                "nonce": str(order_data['nonce']),
                "status": "pending",
                "timestamp": str(int(time.time()))
            }
        )
        # Add to user's order set
        self.redis.sadd(f"user_orders:{order_data['user_address']}", order_id)
        return order_id

    def get_user_nonce(self, user_address: str) -> int:
        """Get the next nonce for a user"""
        nonce = self.redis.get(f"nonce:{user_address}")
        return int(nonce) if nonce else 0

    def increment_user_nonce(self, user_address: str) -> int:
        """Increment and return a user's nonce"""
        return self.redis.incr(f"nonce:{user_address}")

    def update_order_status(self, order_id: str, status: str, tx_hash: Optional[str] = None, error: Optional[str] = None):
        """Update the status of an order"""
        updates = {"status": status}
        if tx_hash:
            updates["transaction_hash"] = tx_hash
        if error:
            updates["error"] = error
        self.redis.hmset(f"order:{order_id}", updates)

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Get order details by ID"""
        order = self.redis.hgetall(f"order:{order_id}")
        return {k.decode(): v.decode() for k, v in order.items()} if order else None

    def get_user_pending_orders(self, user_address: str) -> list[Dict[str, Any]]:
        """Get all pending orders for a user"""
        order_ids = self.redis.smembers(f"user_orders:{user_address}")
        orders = []
        for order_id in order_ids:
            order = self.get_order(order_id.decode())
            if order and order['status'] == 'pending':
                orders.append(order)
        return orders

    async def get_unresolved_markets(self) -> List[Dict]:
        """Get all unresolved markets"""
        try:
            # Get all market keys with unresolved status
            market_keys = self.redis.keys('market:*:status')
            unresolved_markets = []
            
            for key in market_keys:
                status = self.redis.get(key)
                if status == b'unresolved':
                    condition_id = key.decode().split(':')[1]
                    market_data = self.redis.hgetall(f'market:{condition_id}')
                    if market_data:
                        unresolved_markets.append({
                            'condition_id': condition_id,
                            **{k.decode(): v.decode() for k, v in market_data.items()}
                        })
            
            return unresolved_markets
        except Exception as e:
            logger.error(f"Failed to get unresolved markets: {str(e)}")
            return []

    async def get_market_positions(self, condition_id: str) -> List[Dict]:
        """Get all positions for a market"""
        try:
            position_keys = self.redis.smembers(f'market:{condition_id}:positions')
            positions = []
            
            for key in position_keys:
                position_data = self.redis.hgetall(key.decode())
                if position_data:
                    positions.append({
                        k.decode(): v.decode() for k, v in position_data.items()
                    })
            
            return positions
        except Exception as e:
            logger.error(f"Failed to get positions for market {condition_id}: {str(e)}")
            return []

    async def mark_position_redeemed(
        self, 
        condition_id: str, 
        user_address: str, 
        transaction_data: Dict
    ):
        """Mark a position as redeemed"""
        try:
            key = f'position:{condition_id}:{user_address}'
            updates = {
                'status': 'redeemed',
                'redemption_tx': transaction_data.get('redemption_tx', ''),
                'transfer_tx': transaction_data.get('transfer_tx', ''),
                'amount_transferred': str(transaction_data.get('amount_transferred', 0)),
                'redeemed_at': str(int(time.time()))
            }
            self.redis.hmset(key, updates)
        except Exception as e:
            logger.error(f"Failed to mark position as redeemed: {str(e)}")
            raise

    async def mark_market_resolved(
        self, 
        condition_id: str, 
        winning_outcome: int, 
        metadata: Dict
    ):
        """Mark a market as resolved"""
        try:
            # Update market status
            self.redis.set(f'market:{condition_id}:status', 'resolved')
            
            # Update market data
            updates = {
                'status': 'resolved',
                'winning_outcome': str(winning_outcome),
                'resolved_at': str(metadata.get('timestamp', int(time.time()))),
                'processed_at': str(metadata.get('processed_at', int(time.time())))
            }
            self.redis.hmset(f'market:{condition_id}', updates)
        except Exception as e:
            logger.error(f"Failed to mark market as resolved: {str(e)}")
            raise

    async def store_market_position(
        self,
        condition_id: str,
        user_address: str,
        position_data: Dict
    ):
        """Store a new market position"""
        try:
            # Create position key
            position_key = f'position:{condition_id}:{user_address}'
            
            # Prepare position data
            position = {
                'user_address': user_address,
                'condition_id': condition_id,
                'collateral_token': position_data['collateral_token'],
                'outcome': str(position_data['outcome']),
                'amount': str(position_data['amount']),
                'status': 'active',
                'created_at': str(int(time.time()))
            }
            
            # Store position data
            self.redis.hmset(position_key, position)
            
            # Add to market's position set
            self.redis.sadd(f'market:{condition_id}:positions', position_key)
            
            # Mark market as unresolved if not already set
            if not self.redis.exists(f'market:{condition_id}:status'):
                self.redis.set(f'market:{condition_id}:status', 'unresolved')
                
            # Store basic market data if not exists
            if not self.redis.exists(f'market:{condition_id}'):
                self.redis.hmset(f'market:{condition_id}', {
                    'condition_id': condition_id,
                    'status': 'unresolved',
                    'created_at': str(int(time.time()))
                })
                
        except Exception as e:
            logger.error(f"Failed to store market position: {str(e)}")
            raise