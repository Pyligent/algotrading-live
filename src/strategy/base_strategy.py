from abc import ABC, abstractmethod
from typing import Any, Dict, List

from src.data.bar_generator import Bar


class BaseStrategy(ABC):
    def __init__(self, params: Dict[str, Any]):
        self.params = params

    @abstractmethod
    def on_start(self, context: Dict[str, Any]) -> None:
        ...

    @abstractmethod
    def on_bar(self, bar: Bar, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Returns a list of actions, e.g.:
        [
          {"type": "ENTER_LONG", "symbol": "SPY"},
          {"type": "EXIT", "symbol": "QQQ"}
        ]
        """
        ...

    @abstractmethod
    def on_end(self, context: Dict[str, Any]) -> None:
        ...
