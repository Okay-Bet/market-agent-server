from redis import Redis
from typing import Optional, Dict, Any
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
