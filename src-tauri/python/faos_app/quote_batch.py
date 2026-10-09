"""行情合并推送：窗口内每对只保留最新帧，flush 时一次批量输出。"""

from typing import Callable

from faos_app.mqtt_feed import SpreadQuote


class SpreadBatcher:
    """push(pair, q) 入批（后帧覆盖前帧）；flush() 一次性输出 {pair: 最新帧}。"""

    def __init__(self, sink: Callable[[dict], None]):
        self._sink = sink
        self._pending: dict[str, SpreadQuote] = {}

    def push(self, pair: str, q: SpreadQuote) -> None:
        self._pending[pair] = q

    def flush(self) -> None:
        if not self._pending:
            return
        pending, self._pending = self._pending, {}
        self._sink(pending)
