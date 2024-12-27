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

    def get_market(self, identifier: str, by_token_id: bool = False) -> Optional[Dict]:
        """
        Retrieves a market by either condition_id or token_id.
        
        Args:
            identifier: The market identifier (either condition_id or token_id)
            by_token_id: If True, search by token_id instead of condition_id
        """
        db = self.get_db()
        try:
            if by_token_id:
                market = db.query(Market).filter(Market.token_id == identifier).first()
            else:
                market = db.query(Market).filter(Market.condition_id == identifier).first()
                
            if market:
                return {
                    'condition_id': str(market.condition_id),
                    'token_id': str(market.token_id) if market.token_id else None,
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
        """
        Creates a new market entry with both condition_id and token_id.
        
        Args:
            market_data: Dictionary containing:
                - condition_id: Market condition ID
                - token_id: Token ID (optional)
                - status: Market status
                - metadata: Market metadata
        """
        db = self.get_db()
        try:
            # Extract the token_id from market_data if present
            token_id = market_data.get('token_id')
            
            market = Market(
                condition_id=market_data['condition_id'],
                token_id=token_id,
                status=market_data.get('status', 'unresolved'),
                total_volume_usdc=market_data.get('total_volume_usdc', 0),
                market_metadata=market_data.get('metadata', {})
            )
            db.add(market)
            db.commit()
            logger.info(f"Successfully created market {market_data['condition_id']}")
            return str(market.condition_id)
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to create market: {str(e)}")
            raise
        finally:
            db.close()

    def update_market_metadata(self, condition_id: str, metadata: Dict[str, Any]) -> None:
        """
        Updates the metadata for an existing market.
        
        Args:
            condition_id: The market's condition ID
            metadata: Dictionary containing updated market metadata
        """
        db = self.get_db()
        try:
            market = db.query(Market).filter(Market.condition_id == condition_id).first()
            if market:
                # Update metadata while preserving existing fields
                current_metadata = market.market_metadata or {}
                updated_metadata = {**current_metadata, **metadata}
                market.market_metadata = updated_metadata
                db.commit()
                logger.info(f"Updated metadata for market {condition_id}")
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to update market metadata: {str(e)}")
            raise
        finally:
            db.close()

    def record_position(self, position_data: Dict[str, Any]) -> None:
        """
        Records a position ownership after a successful trade.
        Uses SQLAlchemy session for transaction management.
        
        Args:
            position_data: Dictionary containing:
                - user_address: Address of position owner
                - order_id: ID of the executed order
                - token_id: Market token ID
                - amount: Position size
                - price: Entry price
                - side: Trade side (BUY/SELL)
        """
        db = self.get_db()
        try:
            # First record/update the position
            existing_position = db.query(Position).filter(
                Position.user_address == position_data['user_address'],
                Position.condition_id == position_data['condition_id'],
                Position.outcome == position_data['outcome']
            ).first()

            if existing_position:
                # Update existing position
                total_amount = existing_position.amount + position_data['amount']
                # Calculate new average entry price
                existing_position.entry_price = (
                    (existing_position.amount * existing_position.entry_price) +
                    (position_data['amount'] * position_data['price'])
                ) / total_amount
                existing_position.amount = total_amount
            else:
                # Create new position
                new_position = Position(
                    user_address=position_data['user_address'],
                    condition_id=position_data['condition_id'],
                    outcome=position_data['outcome'],
                    amount=position_data['amount'],
                    entry_price=position_data['price'],
                    status='ACTIVE'
                )
                db.add(new_position)

            # Record the order
            order = Order(
                id=position_data['order_id'],
                user_address=position_data['user_address'],
                market_id=position_data['condition_id'],
                price=position_data['price'],
                amount=position_data['amount'],
                side=position_data['side'],
                status='EXECUTED'
            )
            db.add(order)

            db.commit()
            logger.info(f"Successfully recorded position for user {position_data['user_address']}")

        except Exception as e:
            db.rollback()
            logger.error(f"Failed to record position: {str(e)}")
            raise
        finally:
            db.close()

    def get_user_positions(self, user_address: str) -> List[Dict[str, Any]]:
        """
        Retrieves all active positions for a user.
        
        Args:
            user_address: User's blockchain address
            
        Returns:
            List of position dictionaries containing position details
        """
        db = self.get_db()
        try:
            positions = db.query(Position).filter(
                Position.user_address == user_address,
                Position.status == 'ACTIVE'
            ).all()

            return [{
                'user_address': pos.user_address,
                'condition_id': pos.condition_id,
                'token_id': pos.token_id,
                'outcome': pos.outcome,
                'amount': pos.amount,
                'entry_price': pos.entry_price,
                'status': pos.status
            } for pos in positions]

        except Exception as e:
            logger.error(f"Failed to get user positions: {str(e)}")
            raise
        finally:
            db.close()