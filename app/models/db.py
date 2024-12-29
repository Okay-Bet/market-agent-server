from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKey, Index, Boolean, func, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.schema import UniqueConstraint
import uuid
from datetime import datetime

Base = declarative_base()


class User(Base):
    __tablename__ = 'users'
    
    address = Column(String(42), primary_key=True)
    nonce = Column(Integer, nullable=False)
    total_volume_usdc = Column(Numeric(78, 18), nullable=False)
    total_realized_pnl = Column(Numeric(78, 18), nullable=False)
    total_trades = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

class Market(Base):
    __tablename__ = 'markets'

    condition_id = Column(String(256), primary_key=True)
    status = Column(String(20), nullable=False)
    winning_outcome = Column(Integer, nullable=True)
    resolution_price = Column(Numeric(78, 18), nullable=True)
    total_volume_usdc = Column(Numeric(78, 18), nullable=False)
    created_at = Column(DateTime, nullable=False)
    resolved_at = Column(DateTime, nullable=True)
    processed_at = Column(DateTime, nullable=True)
    market_metadata = Column(JSONB, nullable=True)
    token_id = Column(String(256), nullable=True)

    __table_args__ = (
        Index('ix_markets_token_id', 'token_id'),
    )

class Position(Base):
    __tablename__ = 'positions'
    
    id = Column(UUID(as_uuid=True), primary_key=True)
    condition_id = Column(String(66), ForeignKey('markets.condition_id'), nullable=False)
    user_address = Column(String(42), ForeignKey('users.address'), nullable=False)
    collateral_token = Column(String(42), nullable=False)
    outcome = Column(Integer, nullable=False)
    amount = Column(Numeric(78, 18), nullable=False)
    average_entry_price = Column(Numeric(78, 18), nullable=False)
    total_cost_basis = Column(Numeric(78, 18), nullable=False)
    unrealized_pnl = Column(Numeric(78, 18), nullable=True)
    realized_pnl = Column(Numeric(78, 18), nullable=False)
    status = Column(String(20), nullable=False)
    redemption_tx = Column(String(66), nullable=True)
    transfer_tx = Column(String(66), nullable=True)
    amount_transferred = Column(Numeric(78, 18), nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
    redeemed_at = Column(DateTime, nullable=True)
    order_id = Column(String(66), nullable=True)

    __table_args__ = (
        UniqueConstraint('condition_id', 'user_address', 'outcome', 
                        name='uix_position_market_user_outcome'),
        Index('ix_positions_condition_id', 'condition_id'),
        Index('ix_positions_order', 'order_id'),
        Index('ix_positions_status', 'status'),
        Index('ix_positions_user_address', 'user_address'),
        Index('ix_positions_user_market', 'user_address', 'condition_id'),
    )

class Order(Base):
    __tablename__ = 'orders'
    
    id = Column(String(66), primary_key=True)
    user_address = Column(String(42), ForeignKey('users.address'), nullable=False)
    market_id = Column(String(66), nullable=False)
    price = Column(Numeric(78, 18), nullable=False)
    amount = Column(Numeric(78, 18), nullable=False)
    side = Column(String(4), nullable=False)
    nonce = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False)
    block_number = Column(Integer, nullable=True)
    transaction_hash = Column(String(66), nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
    executed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index('ix_orders_status', 'status'),
        Index('ix_orders_user_address', 'user_address'),
        Index('ix_orders_user_status', 'user_address', 'status'),
    )

class Transaction(Base):
    __tablename__ = 'transactions'
    
    id = Column(UUID(as_uuid=True), primary_key=True)
    user_address = Column(String(42), ForeignKey('users.address'), nullable=False)
    condition_id = Column(String(66), ForeignKey('markets.condition_id'), nullable=False)
    transaction_hash = Column(String(66), nullable=False)
    transaction_type = Column(String(20), nullable=False)
    amount = Column(Numeric(78, 18), nullable=False)
    price = Column(Numeric(78, 18), nullable=False)
    usdc_value = Column(Numeric(78, 18), nullable=False)
    realized_pnl = Column(Numeric(78, 18), nullable=True)
    outcome = Column(Integer, nullable=False)
    block_number = Column(Integer, nullable=False)
    block_timestamp = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False)

    __table_args__ = (
        Index('ix_transactions_block', 'block_number'),
        Index('ix_transactions_hash', 'transaction_hash'),
        Index('ix_transactions_market', 'condition_id'),
        Index('ix_transactions_timestamp', 'block_timestamp'),
        Index('ix_transactions_user', 'user_address'),
        Index('ix_transactions_user_market', 'user_address', 'condition_id'),
    )