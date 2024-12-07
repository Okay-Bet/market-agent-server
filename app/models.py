# src/models.py
from pydantic import BaseModel
from typing import List

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