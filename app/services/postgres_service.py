from datetime import datetime
import decimal
import time
import uuid
from sqlalchemy.orm import Session
from sqlalchemy import create_engine, text
from typing import Optional, Dict, Any, List
import hashlib
from ..models.db import User, Market, Position, Order, Transaction
from sqlalchemy.exc import SQLAlchemyError
from ..database import SessionLocal
from ..config import logger, USDC_ADDRESS

class PostgresService:
    def __init__(self):
        self.SessionLocal = SessionLocal

    def get_db(self) -> Session:
        return SessionLocal()

    def execute_query(self, query: str, params: Dict = None) -> List[Dict[str, Any]]:
        """
        Execute a SQL query and return results as a list of dictionaries.
        
        Args:
            query: SQL query string
            params: Optional dictionary of query parameters
            
        Returns:
            List of dictionaries containing query results
        """
        with self.SessionLocal() as session:
            try:
                result = session.execute(text(query), params or {})
                
                if result.returns_rows:
                    # Get column names from result.keys()
                    columns = result.keys()
                    
                    # Convert each row to a dictionary using column names
                    return [
                        {col: getattr(row, col) for col in columns}
                        for row in result
                    ]
                return []
                
            except SQLAlchemyError as e:
                logger.error(f"Database query failed: {str(e)}", exc_info=True)
                session.rollback()
                raise
            except Exception as e:
                logger.error(f"Unexpected error during query execution: {str(e)}", exc_info=True)
                session.rollback()
                raise

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

    def get_unresolved_markets(self) -> List[Dict[str, Any]]:
        """
        Fetch all unresolved markets with diagnostic logging.
        
        We'll step through the diagnostics carefully to identify any data issues.
        """
        try:
            # First, let's check total market count with explicit column naming
            count_query = """
                SELECT COUNT(*) as total_count 
                FROM markets
            """
            total_result = self.execute_query(count_query)
            total_count = total_result[0]['total_count']

            # Check markets by status with explicit column names
            status_query = """
                SELECT 
                    status, 
                    COUNT(*) as status_count 
                FROM markets 
                GROUP BY status
            """
            status_counts = self.execute_query(status_query)

            # Main query with explicit parameters
            main_query = """
                SELECT 
                    condition_id,
                    token_id,
                    status,
                    market_metadata,
                    created_at,
                    resolved_at,
                    processed_at
                FROM markets 
                WHERE status = :status
                AND token_id IS NOT NULL
                ORDER BY created_at DESC
            """
            
            results = self.execute_query(main_query, {"status": "unresolved"})
            logger.info(f"Found {len(results)} unresolved markets with token_id")
            
            return results

        except SQLAlchemyError as e:
            logger.error("Failed to fetch unresolved markets", exc_info=True)
            raise

    def get_market_positions(self, condition_id: str) -> List[Dict[str, Any]]:
        """
        Get all positions for a specific market.
        
        Args:
            condition_id: Market condition ID
            
        Returns:
            List of position dictionaries
        """
        query = """
            SELECT 
                user_address,
                outcome,
                amount,
                collateral_token
            FROM positions 
            WHERE condition_id = :condition_id
            AND status = 'active'
        """
        
        try:
            return self.execute_query(query, {"condition_id": condition_id})
        except SQLAlchemyError as e:
            logger.error(f"Failed to fetch positions for market {condition_id}", exc_info=True)
            raise

    def mark_position_redeemed(
        self,
        condition_id: str,
        user_address: str,
        redemption_data: Dict[str, Any]
    ) -> None:
        """
        Mark a position as redeemed after successful processing.
        
        Args:
            condition_id: Market condition ID
            user_address: Address of position holder
            redemption_data: Transaction details and amounts
        """
        query = """
            UPDATE positions 
            SET 
                status = 'redeemed',
                redemption_tx = :redemption_tx,
                transfer_tx = :transfer_tx,
                amount_transferred = :amount_transferred,
                redeemed_at = CURRENT_TIMESTAMP
            WHERE condition_id = :condition_id
            AND user_address = :user_address
        """
        
        params = {
            "condition_id": condition_id,
            "user_address": user_address,
            **redemption_data
        }
        
        try:
            self.execute_query(query, params)
        except SQLAlchemyError as e:
            logger.error(f"Failed to mark position as redeemed for user {user_address}", exc_info=True)
            raise

    def mark_market_resolved(
        self,
        condition_id: str,
        winning_outcome: int,
        metadata: Dict[str, Any]
    ) -> None:
        """
        Mark a market as resolved with its winning outcome.
        
        Args:
            condition_id: Market condition ID
            winning_outcome: 0 for NO, 1 for YES
            metadata: Additional metadata like timestamps
        """
        query = """
            UPDATE markets 
            SET 
                status = 'resolved',
                winning_outcome = :winning_outcome,
                resolved_at = :resolved_at,
                processed_at = :processed_at
            WHERE condition_id = :condition_id
        """
        
        params = {
            "condition_id": condition_id,
            "winning_outcome": winning_outcome,
            "resolved_at": metadata.get("timestamp"),
            "processed_at": metadata.get("processed_at")
        }
        
        try:
            self.execute_query(query, params)
        except SQLAlchemyError as e:
            logger.error(f"Failed to mark market {condition_id} as resolved", exc_info=True)
            raise

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

    def create_market(self, market_data: Dict[str, Any]) -> str:
        """
        Creates a new market entry with enhanced validation and data processing.
        
        Args:
            market_data: Dictionary containing:
                - condition_id: Market condition ID
                - market_id: Market ID from the token (required)
                - metadata: Market metadata including outcomes, prices
        """
        db = self.get_db()
        try:
            # Extract and validate token_id (market_id)
            token_id = market_data.get('market_id')
            if not token_id:
                raise ValueError("market_id is required for market creation")

            # Process metadata to determine initial status
            metadata = market_data.get('metadata', {})
            outcome_prices = metadata.get('outcome_prices', [])
            
            # Convert string representation to list if necessary
            if isinstance(outcome_prices, str):
                import ast
                outcome_prices = ast.literal_eval(outcome_prices)
                
            # Determine initial status based on prices
            initial_status = 'unresolved'
            winning_outcome = None
            
            if outcome_prices == [1.0, 0.0]:
                initial_status = 'resolved'
                winning_outcome = 1
            elif outcome_prices == [0.0, 1.0]:
                initial_status = 'resolved'
                winning_outcome = 0

            market = Market(
                condition_id=market_data['condition_id'],
                token_id=token_id,
                status=initial_status,
                winning_outcome=winning_outcome,
                total_volume_usdc=market_data.get('total_volume_usdc', 0),
                market_metadata=metadata,
                created_at=market_data.get('created_at', time.now()),
                resolved_at=time.now() if initial_status == 'resolved' else None
            )
            
            db.add(market)
            db.commit()
            
            logger.info(f"Created market {token_id} with status {initial_status}")
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
        Records a position ownership after a successful trade, ensuring all required
        records exist in the database first.
        """
        db = self.get_db()
        try:
            with db.begin_nested():
                # 1. First, ensure user exists
                user_address = position_data['user_address']
                user = db.query(User).filter(User.address == user_address).first()
                
                if not user:
                    user = User(
                        address=user_address,
                        nonce=0,
                        total_volume_usdc=decimal.Decimal('0'),
                        total_realized_pnl=decimal.Decimal('0'),
                        total_trades=0,
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow()
                    )
                    db.add(user)
                    db.flush()  # Ensure user is created before continuing
                    logger.info(f"Created new user record for address: {user_address}")

                # 2. Ensure market exists
                condition_id = position_data['condition_id']
                market = db.query(Market).filter(Market.condition_id == condition_id).first()
                
                if not market:
                    # Create basic market record if it doesn't exist
                    market = Market(
                        condition_id=condition_id,
                        status='active',
                        total_volume_usdc=decimal.Decimal('0'),
                        created_at=datetime.utcnow()
                    )
                    db.add(market)
                    db.flush()  # Ensure market is created before continuing
                    logger.info(f"Created new market record for condition: {condition_id}")

                # 3. Now create the position
                amount = decimal.Decimal(str(position_data['amount']))
                price = decimal.Decimal(str(position_data['price']))
                current_time = datetime.utcnow()
                cost_basis = amount * price

                existing_position = db.query(Position).filter(
                    Position.user_address == user_address,
                    Position.condition_id == condition_id,
                    Position.outcome == position_data['outcome']
                ).first()

                if existing_position:
                    # Update existing position logic
                    total_amount = existing_position.amount + amount
                    existing_position.average_entry_price = (
                        (existing_position.amount * existing_position.average_entry_price +
                        amount * price) / total_amount
                    )
                    existing_position.amount = total_amount
                    existing_position.total_cost_basis += cost_basis
                    existing_position.updated_at = current_time
                else:
                    # Create new position
                    new_position = Position(
                        id=uuid.uuid4(),
                        user_address=user_address,
                        condition_id=condition_id,
                        outcome=position_data['outcome'],
                        amount=amount,
                        average_entry_price=price,
                        collateral_token=USDC_ADDRESS,
                        total_cost_basis=cost_basis,
                        unrealized_pnl=decimal.Decimal('0'),
                        realized_pnl=decimal.Decimal('0'),
                        status='active',
                        created_at=current_time,
                        updated_at=current_time,
                        order_id=position_data['order_id']
                    )
                    db.add(new_position)

                # 4. Commit everything in one transaction
                db.commit()
                logger.info(f"Successfully recorded all records for user {user_address}")

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

    def get_pending_redemptions(self) -> List[Dict[str, Any]]:
        """
        Fetch resolved markets that need position redemption processing.
        
        Returns:
            List of market dictionaries that are resolved but not fully processed
        """
        query = """
            SELECT 
                m.condition_id,
                m.token_id,
                m.status,
                m.winning_outcome,
                m.market_metadata,
                m.created_at,
                m.resolved_at,
                COUNT(p.id) as position_count
            FROM markets m
            LEFT JOIN positions p ON 
                m.condition_id = p.condition_id 
                AND p.status = 'active'
            WHERE 
                m.status = 'resolved'
                AND m.winning_outcome IS NOT NULL
                AND (m.processed_at IS NULL OR EXISTS (
                    SELECT 1 FROM positions 
                    WHERE condition_id = m.condition_id 
                    AND status = 'active'
                ))
            GROUP BY 
                m.condition_id,
                m.token_id,
                m.status,
                m.winning_outcome,
                m.market_metadata,
                m.created_at,
                m.resolved_at
            ORDER BY m.resolved_at ASC
        """
        
        try:
            results = self.execute_query(query)
            logger.info(f"Found {len(results)} markets pending redemption processing")
            for market in results:
                logger.info(f"Market {market['condition_id']} has {market['position_count']} active positions")
            return results
        except SQLAlchemyError as e:
            logger.error("Failed to fetch pending redemptions", exc_info=True)
            raise

    def mark_market_processed(self, condition_id: str) -> None:
        """
        Mark a market as fully processed after handling all redemptions.
        
        Args:
            condition_id: Market condition ID
        """
        query = """
            UPDATE markets 
            SET 
                processed_at = CURRENT_TIMESTAMP
            WHERE condition_id = :condition_id
            AND NOT EXISTS (
                SELECT 1 FROM positions 
                WHERE condition_id = :condition_id 
                AND status = 'active'
            )
        """
        
        try:
            self.execute_query(query, {"condition_id": condition_id})
            logger.info(f"Market {condition_id} marked as processed")
        except SQLAlchemyError as e:
            logger.error(f"Failed to mark market {condition_id} as processed: {str(e)}")
            raise

    def get_winning_positions(self, condition_id: str, winning_outcome: int) -> List[Dict[str, Any]]:
        """
        Get all winning positions for a resolved market.
        
        Args:
            condition_id: Market condition ID
            winning_outcome: The winning outcome (0 or 1)
            
        Returns:
            List of position dictionaries for winners
        """
        query = """
            SELECT 
                p.user_address,
                p.outcome,
                p.amount,
                p.collateral_token,
                p.entry_price,
                u.total_volume_usdc
            FROM positions p
            JOIN users u ON p.user_address = u.address
            WHERE 
                p.condition_id = :condition_id
                AND p.outcome = :winning_outcome
                AND p.status = 'active'
        """
        
        try:
            positions = self.execute_query(query, {
                "condition_id": condition_id,
                "winning_outcome": winning_outcome
            })
            logger.info(f"Found {len(positions)} winning positions for market {condition_id}")
            return positions
        except SQLAlchemyError as e:
            logger.error(f"Failed to fetch winning positions for market {condition_id}: {str(e)}")
            raise