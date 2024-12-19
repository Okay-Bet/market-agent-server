# src/models.py
from pydantic import BaseModel, Field, confloat, conint
from typing import List, Optional, Literal
from enum import Enum


class OrderRequest(BaseModel):
    user_address: str = Field(..., description="Ethereum address of the user")
    token_id: str
    side: str
    price: confloat(gt=0, lt=1) = Field(description="Price must be between 0 and 1")
    amount: conint(gt=0) = Field(description="Amount must be a positive integer")
    is_yes_token: bool

class Position(BaseModel):
    market_id: str
    token_id: str
    market_question: str
    outcomes: List[str]
    prices: List[float]
    balances: List[float]
    entry_prices: Optional[List[float]] = None
    timestamp: Optional[str] = None

class SellPositionRequest(BaseModel):
    token_id: str
    amount: float

class SignedOrder(BaseModel):
    market_id: str
    price: float
    amount: float
    side: Literal["BUY", "SELL"]
    nonce: int
    user_address: str
    signature: str

class OrderStatus(BaseModel):
    order_id: str
    status: Literal["pending", "executing", "completed", "failed"]
    error: Optional[str] = None
    transaction_hash: Optional[str] = None