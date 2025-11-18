from typing import List
import numpy as np


def equal_weight_portfolio(symbols: List[str]) -> List[float]:
    n = len(symbols)
    if n == 0:
        return []
    return list(np.ones(n) / n)
