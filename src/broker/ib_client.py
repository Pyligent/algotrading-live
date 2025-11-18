from typing import Dict

from ib_insync import IB, Stock, MarketOrder, Contract

from src.core.config_loader import IBConfig, UniverseConfig
from src.core.enums import OrderSide
from src.core.logging_utils import get_logger
from src.broker.order_types import OrderRequest

logger = get_logger(__name__)


class IBClient:
    def __init__(self, ib_config: IBConfig, universe_config: UniverseConfig):
        self.ib_config = ib_config
        self.universe_config = universe_config
        self.ib = IB()
        self._contracts: Dict[str, Contract] = {}
        self.active_client_id: int | None = None

    def connect(self):
        base_cid = self.ib_config.client_id
        last_err: Exception | None = None
        attempts = max(1, self.ib_config.max_client_id_attempts)
        for cid in range(base_cid, base_cid + attempts):
            try:
                logger.info(
                    f"Connecting to IBKR at {self.ib_config.host}:{self.ib_config.port} "
                    f"client_id={cid}"
                )
                self.ib.connect(
                    self.ib_config.host, self.ib_config.port, clientId=cid
                )
                self.active_client_id = cid
                logger.info("Connected to IBKR")
                break
            except Exception as e:
                last_err = e
                logger.warning(f"Connect failed for client_id={cid}: {e}")
        if not self.ib.isConnected():
            raise RuntimeError(f"Failed to connect to IBKR: {last_err}") from last_err
        try:
            accounts = self.ib.managedAccounts()
        except Exception:
            accounts = []
        if self.ib_config.account_id:
            if self.ib_config.account_id not in accounts:
                logger.error(
                    f"Configured account_id '{self.ib_config.account_id}' not in managed accounts: {accounts}"
                )
                raise RuntimeError(
                    f"IB account mismatch. Expected {self.ib_config.account_id}, got {accounts}"
                )
            logger.info(f"Using account_id={self.ib_config.account_id}")
        else:
            logger.info(f"Managed accounts: {accounts or 'unknown'} (no account_id configured)")

    def disconnect(self):
        logger.info("Disconnecting from IBKR")
        self.ib.disconnect()

    def get_contract(self, symbol: str) -> Contract:
        if symbol in self._contracts:
            return self._contracts[symbol]
        c = Stock(
            symbol,
            self.universe_config.primary_exchange,
            self.universe_config.currency,
        )
        c = self.ib.qualifyContracts(c)[0]
        self._contracts[symbol] = c
        return c

    def place_market_order(self, order_request: OrderRequest):
        contract = self.get_contract(order_request.symbol)
        action = "BUY" if order_request.side == OrderSide.BUY else "SELL"
        ib_order = MarketOrder(action, order_request.quantity)
        if self.ib_config.account_id:
            ib_order.account = self.ib_config.account_id
        logger.info(
            f"Placing market order: {order_request.symbol} {action} x{order_request.quantity}"
        )
        trade = self.ib.placeOrder(contract, ib_order)
        return trade

    def get_positions(self) -> Dict[str, int]:
        positions = self.ib.positions()
        pos_map: Dict[str, int] = {}
        for p in positions:
            symbol = p.contract.symbol
            pos_map[symbol] = pos_map.get(symbol, 0) + int(p.position)
        return pos_map

    def flatten_symbol(self, symbol: str):
        positions = self.get_positions()
        qty = positions.get(symbol, 0)
        if qty == 0:
            return
        side = OrderSide.SELL if qty > 0 else OrderSide.BUY
        self.place_market_order(OrderRequest(symbol=symbol, quantity=abs(qty), side=side))
        logger.info(f"Flattened {symbol}, closed {qty} shares.")
