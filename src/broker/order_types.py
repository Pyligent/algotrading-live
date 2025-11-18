from dataclasses import dataclass
from src.core.enums import OrderSide


@dataclass
class OrderRequest:
    symbol: str
    quantity: int
    side: OrderSide
