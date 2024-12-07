# src/models.py
from pydantic import BaseModel
from typing import List, Optional, Literal

class OrderRequest(BaseModel):
    market_id: str
    price: float
    amount: float
    side: str

class Position(BaseModel):
    market_id: str
    token_id: str
    market_question: str
    outcomes: List[str]
    prices: List[float]
    balances: List[float]

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