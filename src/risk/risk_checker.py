from src.core.enums import OrderSide
from src.broker.order_types import OrderRequest
from src.risk.risk_limits import RiskManager
from src.core.logging_utils import get_logger
from src.core.config_loader import SymbolOverride

logger = get_logger(__name__)


def size_order(
    symbol: str,
    side: OrderSide,
    price: float,
    stop_distance_pct: float,
    risk_manager: RiskManager,
    symbol_overrides: dict[str, SymbolOverride] | None = None,
) -> OrderRequest | None:
    allowed_risk = risk_manager.allowed_risk_per_trade_for_symbol(symbol, symbol_overrides)
    risk_per_share = price * (stop_distance_pct / 100.0)
    if risk_per_share <= 0:
        return None
    qty = int(allowed_risk / risk_per_share)
    if qty <= 0:
        return None
    logger.info(
        f"Sized order for {symbol}: {side.value} {qty} shares at {price}, "
        f"risk per trade ~{allowed_risk:.2f}"
    )
    return OrderRequest(symbol=symbol, quantity=qty, side=side)


def should_flatten_all(risk_manager: RiskManager) -> bool:
    return risk_manager.is_daily_loss_limit_hit()
