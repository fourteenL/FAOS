"""MQTT 价差行情服务（api.md 协议对接）。

主题：
- 订阅 `tq/spread/{user}/#` 与 `tq/status/{user}`（服务端推送）
- 向 `tq/sub/{user}` / `tq/unsub/{user}` 发布控制消息（QoS1，禁止 retain）

paho 回调运行在 paho 网络线程；所有对上层回调的调用经 dispatch
（生产 = portal.start_task_soon，测试 = 同步直调）转发，保证线程安全。
"""

import json
import math
from typing import Callable, Optional

from paho.mqtt.client import CallbackAPIVersion, Client, MQTTMessage
from pydantic import BaseModel

from faos_app.config import MqttSettings


class SpreadQuote(BaseModel):
    short_open: float
    long_open: float
    short_cover: float
    long_cover: float


class MqStatus(BaseModel):
    subscribed: list[str] = []
    products: list[str] = []
    invalid: list = []
    pending: list[str] = []
    error: Optional[str] = None


def parse_spread_topic(topic: str) -> Optional[str]:
    """`tq/spread/{user}/{a}&{b}` -> `{a}&{b}`；非 spread 主题返回 None。"""
    prefix = f"tq/spread/"
    if not topic.startswith(prefix):
        return None
    rest = topic[len(prefix):]
    _, _, pair = rest.partition("/")
    if not pair or "/" in pair:
        return None
    return pair


def parse_quote_payload(raw: bytes) -> SpreadQuote:
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise ValueError(f"价差消息不是合法 JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("价差消息结构错误")
    for k in ("short_open", "long_open", "short_cover", "long_cover"):
        v = data.get(k)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ValueError(f"价差字段 {k} 非数值")
        if math.isnan(v) or math.isinf(v):
            raise ValueError(f"价差字段 {k} 无效")
    return SpreadQuote.model_validate(data)


def parse_status_payload(raw: bytes) -> MqStatus:
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise ValueError(f"状态消息不是合法 JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("状态消息结构错误")
    return MqStatus.model_validate(data)


Dispatch = Callable[[Callable, tuple], None]


class MqttFeedService:
    def __init__(
        self,
        settings: MqttSettings,
        portal,
        on_spread: Callable[[str, SpreadQuote], None],
        on_status: Callable[[MqStatus], None],
        on_state: Callable[[str], None],
        client_factory: Optional[Callable[[], Client]] = None,
        dispatch: Optional[Dispatch] = None,
    ):
        self._s = settings
        self._portal = portal
        self._on_spread = on_spread
        self._on_status = on_status
        self._on_state = on_state
        if dispatch is not None:
            self._dispatch = dispatch
        else:
            def dispatch_via_portal(fn, *args):
                portal.start_task_soon(fn, *args)
            self._dispatch = dispatch_via_portal

        self._client = (client_factory() if client_factory else self._make_client())
        self._client.username_pw_set(self._s.user, self._s.password)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect
        self._stopping = False

    # -- paho client ------------------------------------------------------

    def _make_client(self) -> Client:
        return Client(CallbackAPIVersion.VERSION1, client_id=f"{self._s.user}-faos-1",
                      clean_session=False)

    def start(self) -> None:
        """手动连接入口（幂等）：重复调用不重复拨号。"""
        if getattr(self, "_started", False):
            return
        self._started = True
        self._stopping = False
        # connect_async：broker 不可达时不抛异常、loop 内自动重连，
        # 应用继续运行（UI 显示 reconnecting）
        self._client.connect_async(self._s.host, self._s.port, keepalive=60)
        self._client.loop_start()

    def stop(self) -> None:
        self._stopping = True
        self._client.disconnect()
        self._client.loop_stop()

    # -- paho callbacks（paho 线程内执行） ----------------------------------

    def _on_connect(self, client, userdata, flags, rc, props=None) -> None:
        if rc != 0:
            reason = {2: "客户端标识符被拒绝", 4: "用户名或密码错误（鉴权失败）",
                      5: "未通过鉴权（账号/密码/ACL）"}.get(rc, f"连接被拒绝 rc={rc}")
            self._dispatch(self._safe_state, f"error: {reason}")
            return
        client.subscribe(f"tq/spread/{self._s.user}/#", qos=1)
        client.subscribe(f"tq/status/{self._s.user}", qos=1)
        self._dispatch(self._safe_state, "connected")

    def _on_disconnect(self, client, userdata, rc, props=None) -> None:
        if not self._stopping:
            self._dispatch(self._safe_state, "reconnecting")

    def _on_message(self, client, userdata, msg: MQTTMessage) -> None:
        if msg.topic == f"tq/status/{self._s.user}":
            try:
                status = parse_status_payload(msg.payload)
            except ValueError:
                return
            self._dispatch(self._safe_status, status)
            return
        pair = parse_spread_topic(msg.topic)
        if pair is None:
            return
        if getattr(msg, "retain", False):
            return  # 订阅时 broker 补发的上次会话残留（retain），丢弃：只显示实时行情
        try:
            quote = parse_quote_payload(msg.payload)
        except ValueError:
            return  # 无效帧（QoS1 乱序边界）：忽略，不中断监控
        self._dispatch(self._safe_spread, pair, quote)

    # -- 经 dispatch 转发的安全回调 ----------------------------------------

    def _safe_spread(self, pair: str, quote: SpreadQuote) -> None:
        try:
            self._on_spread(pair, quote)
        except Exception as e:  # 回调异常不得杀死 paho/portal 线程
            self._on_state("error: " + str(e))

    def _safe_status(self, status: MqStatus) -> None:
        try:
            self._on_status(status)
        except Exception as e:
            self._on_state("error: " + str(e))

    def _safe_state(self, state: str) -> None:
        self._on_state(state)

    # -- 控制消息（客户端 -> 服务） -----------------------------------------

    def _publish_control(self, action: str, pairs: list[str]) -> None:
        payload = json.dumps({"pairs": pairs}, ensure_ascii=False)
        self._client.publish(f"tq/{action}/{self._s.user}", payload, qos=1, retain=False)

    def subscribe_pairs(self, pairs: list[str]) -> None:
        if pairs:
            self._publish_control("sub", list(pairs))

    def unsubscribe_pairs(self, pairs: list[str]) -> None:
        if pairs:
            self._publish_control("unsub", list(pairs))
