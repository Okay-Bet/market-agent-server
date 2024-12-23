from sqlalchemy.orm import Session
from typing import Optional, Dict, Any, List
import hashlib
from ..models.db import User, Market, Position, Order, Transaction
from ..database import SessionLocal
from ..config import logger

class PostgresService:
    def __init__(self):
        self.SessionLocal = SessionLocal

    def get_db(self) -> Session:
        return SessionLocal()

    def generate_order_id(self, user_address: str, nonce: int) -> str:
        return hashlib.sha256(f"{user_address}:{nonce}".encode()).hexdigest()

    def store_pending_order(self, order_data: Dict[str, Any]) -> str:
        db = self.get_db()
        try:
            order_id = self.generate_order_id(order_data['user_address'], order_data['nonce'])
            order = Order(
                id=order_id,
                user_address=order_data['user_address'],
                market_id=order_data['market_id'],
                price=order_data['price'],
                amount=order_data['amount'], 
                side=order_data['side'],
                nonce=order_data['nonce'],
                status='pending'
            )
            db.add(order)
            db.commit()
            return order_id
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to store pending order: {str(e)}")
            raise
        finally:
            db.close()

    def get_user_nonce(self, user_address: str) -> int:
        db = self.get_db()
        try:
            user = db.query(User).filter(User.address == user_address).first()
            return user.nonce if user else 0
        finally:
            db.close()

    def increment_user_nonce(self, user_address: str) -> int:
        db = self.get_db()
        try:
            user = db.query(User).filter(User.address == user_address).first()
            if not user:
                user = User(address=user_address, nonce=1)
                db.add(user)
            else:
                user.nonce += 1
            db.commit()
            return user.nonce
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to increment nonce: {str(e)}")
            raise
        finally:
            db.close()

    def update_order_status(self, order_id: str, status: str, tx_hash: Optional[str] = None, error: Optional[str] = None):
        db = self.get_db()
        try:
            order = db.query(Order).filter(Order.id == order_id).first()
            if order:
                order.status = status
                if tx_hash:
                    order.transaction_hash = tx_hash
                if error:
                    order.error = error
                db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to update order status: {str(e)}")
            raise
        finally:
            db.close()

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        db = self.get_db()
        try:
            order = db.query(Order).filter(Order.id == order_id).first()
            if order:
                return {
                    'id': str(order.id),
                    'user_address': str(order.user_address),
                    'market_id': str(order.market_id),
                    'price': str(order.price),
                    'amount': str(order.amount),
                    'side': str(order.side),
                    'nonce': int(order.nonce),
                    'status': str(order.status),
                    'transaction_hash': str(order.transaction_hash) if order.transaction_hash else None,
                    'error': str(order.error) if order.error else None
                }
            return None
        finally:
            db.close()

    def get_user_pending_orders(self, user_address: str) -> List[Dict[str, Any]]:
        db = self.get_db()
        try:
            orders = db.query(Order).filter(
                Order.user_address == user_address,
                Order.status == 'pending'
            ).all()
            
            return [{
                'id': str(order.id),
                'market_id': str(order.market_id),
                'price': str(order.price),
                'amount': str(order.amount),
                'side': str(order.side),
                'status': str(order.status),
                'transaction_hash': str(order.transaction_hash) if order.transaction_hash else None,
                'error': str(order.error) if order.error else None
            } for order in orders]
        finally:
            db.close()

    def get_unresolved_markets(self) -> List[Dict]:
        db = self.get_db()
        try:
            markets = db.query(Market).filter(Market.status == 'unresolved').all()
            return [{
                'condition_id': str(market.condition_id),
                'status': str(market.status),
                **(market.market_metadata if market.market_metadata else {})
            } for market in markets]
        finally:
            db.close()

    def get_market_positions(self, condition_id: str) -> List[Dict]:
        db = self.get_db()
        try:
            positions = db.query(Position).filter(
                Position.condition_id == condition_id
            ).all()
            
            return [{
                'user_address': str(pos.user_address),
                'collateral_token': str(pos.collateral_token),
                'outcome': str(pos.outcome),
                'amount': str(pos.amount),
                'status': str(pos.status)
            } for pos in positions]
        finally:
            db.close()

    def mark_position_redeemed(self, condition_id: str, user_address: str, transaction_data: Dict):
        db = self.get_db()
        try:
            position = db.query(Position).filter(
                Position.condition_id == condition_id,
                Position.user_address == user_address
            ).first()
            
            if position:
                position.status = 'redeemed'
                position.redemption_tx = transaction_data.get('redemption_tx')
                position.transfer_tx = transaction_data.get('transfer_tx')
                position.amount_transferred = transaction_data.get('amount_transferred')
                db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to mark position as redeemed: {str(e)}")
            raise
        finally:
            db.close()

    def mark_market_resolved(self, condition_id: str, winning_outcome: int, metadata: Dict):
        db = self.get_db()
        try:
            market = db.query(Market).filter(Market.condition_id == condition_id).first()
            if market:
                market.status = 'resolved'
                market.winning_outcome = winning_outcome
                market.resolved_at = metadata.get('timestamp')
                market.processed_at = metadata.get('processed_at')
                db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to mark market as resolved: {str(e)}")
            raise
        finally:
            db.close()

    def get_market(self, condition_id: str) -> Optional[Dict]:
        db = self.get_db()
        try:
            market = db.query(Market).filter(Market.condition_id == condition_id).first()
            if market:
                return {
                    'condition_id': str(market.condition_id),
                    'status': str(market.status),
                    'winning_outcome': int(market.winning_outcome) if market.winning_outcome else None,
                    'market_metadata': market.market_metadata,
                    'created_at': market.created_at.isoformat() if market.created_at else None,
                    'resolved_at': market.resolved_at.isoformat() if market.resolved_at else None
                }
            return None
        finally:
            db.close()

    def create_market(self, market_data: Dict[str, Any]):
        db = self.get_db()
        try:
            market = Market(
                condition_id=market_data['condition_id'],
                status=market_data.get('status', 'unresolved'),
                market_metadata=market_data.get('metadata', {})
            )
            db.add(market)
            db.commit()
            return str(market.condition_id)
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to create market: {str(e)}")
            raise
        finally:
            db.close()