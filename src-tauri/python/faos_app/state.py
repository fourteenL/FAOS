"""AppState：服务容器与连接状态缓存（纯逻辑，不含 pytauri 导入）。"""

from typing import Optional


class AppState:
    def __init__(self, settings, store, feed, manager, monitor, pusher,
                 ctp=None, spread_service=None, leg_ops=None):
        self.settings = settings
        self.store = store
        self.feed = feed
        self.manager = manager
        self.monitor = monitor
        self.pusher = pusher
        self.ctp = ctp                    # T9 接入
        self.spread_service = spread_service  # T10 接入
        self.leg_ops = leg_ops            # T11 接入
        self.mqtt_state = "disconnected"
        self.ctp_state = "disconnected"

    def set_mqtt_state(self, state: str) -> None:
        self.mqtt_state = state

    def set_ctp_state(self, state: str, msg: Optional[str] = None) -> None:
        self.ctp_state = state

    def app_status(self) -> dict:
        return {
            "mqtt": self.mqtt_state,
            "ctp": self.ctp_state,
            "products": list(getattr(self.settings, "products", [])),
            "product_ticks": dict(getattr(self.settings, "product_ticks", {})),
        }
