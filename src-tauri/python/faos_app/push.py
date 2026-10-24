"""事件推送（Python -> 前端）。事件名前缀 py://，载荷统一 dict。"""

from typing import Optional

from pydantic import RootModel
from pytauri import AppHandle, Emitter

from faos_app.models import Strategy


class _Payload(RootModel[dict]):
    pass


def _wrap(d: dict) -> _Payload:
    return _Payload(d)


class Pusher:
    def __init__(self, app_handle: AppHandle):
        self._h = app_handle
        self.on_conn_state = None  # (kind, state) 回调，AppState 同步用

    def _emit(self, name: str, payload: dict) -> None:
        Emitter.emit(self._h, f"py://{name}", _wrap(payload))

    def strategy(self, s: Strategy) -> None:
        self._emit("strategy", {"strategy": s.model_dump(mode="json")})

    def strategy_deleted(self, strategy_id: int) -> None:
        self._emit("strategy-deleted", {"id": strategy_id})

    def notice(self, level: str, msg: str) -> None:
        self._emit("notice", {"level": level, "msg": msg})

    def spread(self, payload: dict) -> None:
        """实时合成价差（填价参考显示）：{pair, short_open, long_open, live}。"""
        self._emit("spread", payload)

    def order(self, data: dict) -> None:
        self._emit("order", data)

    def trade(self, data: dict) -> None:
        self._emit("trade", data)

    def algo(self, data: dict) -> None:
        self._emit("algo", data)

    def conn_state(self, kind: str, state: str, msg: Optional[str] = None) -> None:
        self._emit("conn-state", {"kind": kind, "state": state, "msg": msg})
        if self.on_conn_state is not None:
            try:
                self.on_conn_state(kind, state)
            except Exception:
                pass
