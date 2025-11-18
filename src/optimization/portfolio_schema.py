from pydantic import BaseModel
from typing import List


class PortfolioTarget(BaseModel):
    symbols: List[str]
    weights: List[float]
