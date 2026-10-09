"""IPC 命令层（pytauri Commands）。仅做参数转换与委托，业务在服务层。

错误约定：ValueError（中文消息）→ 推送 notice(error) 后 re-raise，
pytauri 将异常转为 invoke reject，前端 toast 展示。
"""

from typing import Annotated, Optional

from pydantic import BaseModel, RootModel
from pytauri import Commands, State

from faos_app.auth import apply_login
from faos_app.config import (
    Settings,
    masked_settings,
    merge_masked_settings,
)
from faos_app.config import save_settings as persist_settings
from faos_app.config import settings_path
from faos_app.models import Strategy, TriggerAction
from faos_app.state import AppState

commands: Commands = Commands()


class Empty(BaseModel):
    pass


class Ok(BaseModel):
    ok: bool = True


class DictResp(RootModel[dict]):
    pass


class StrategyListResp(RootModel[list]):
    pass


class CreateBody(BaseModel):
    mode: str  # "pair" | "product"
    leg_a: Optional[str] = None
    leg_b: Optional[str] = None
    product: Optional[str] = None
    single_lots: float = 1
    total_lots: float = 1


class IdBody(BaseModel):
    strategy_id: int


class SetLotsBody(IdBody):
    single_lots: Optional[float] = None
    total_lots: Optional[float] = None


class PlaceTriggerBody(IdBody):
    action: str
    spread: float
    lots: Optional[float] = None


class CancelTriggerBody(IdBody):
    trigger_id: str


class LegBody(IdBody):
    leg: str  # "A" | "B"


class LoginBody(BaseModel):
    ctp_account: str
    ctp_password: str
    counter: str = "实盘"  # SimNow 新前置要求生产证书（实盘）；「测试」会 4097 握手失败
    mqtt_host: str = ""
    mqtt_port: Optional[int] = None  # 空/非法不改配置
    ctp_td: str = ""  # 交易前置（空=不改配置）
    ctp_md: str = ""  # 行情前置（空=不改配置）
    broker: str = "9999"
    remember: bool = False
    offline: bool = False


class LoginResp(BaseModel):
    ok: bool = True
    offline: bool = False


class LoginStateResp(BaseModel):
    ctp_account: str = ""
    ctp_password: str = ""
    counter: str = "实盘"
    mqtt_host: str = ""
    mqtt_port: Optional[int] = None
    ctp_td: str = ""
    ctp_md: str = ""
    broker: str = "9999"
    remembered: bool = False


class SetConfigBody(IdBody):
    slip_a: Optional[int] = None
    slip_b: Optional[int] = None
    order_type_a: Optional[str] = None
    order_type_b: Optional[str] = None


class StopDirectionBody(IdBody):
    side: str  # "short" | "long"


class CancelLegResp(BaseModel):
    cancelled: int


def _guard(state: AppState, fn):
    """执行 fn()；ValueError → notice(error) 后 re-raise。"""
    try:
        return fn()
    except ValueError as e:
        state.pusher.notice("error", str(e))
        raise


@commands.command()
async def ping(body: Empty, state: Annotated[AppState, State()]) -> Ok:
    return Ok()


@commands.command()
async def get_settings(body: Empty, state: Annotated[AppState, State()]) -> Settings:
    return masked_settings(state.settings)  # 密码回显掩码


@commands.command()
async def save_settings(body: Settings, state: Annotated[AppState, State()]) -> Ok:
    merged = merge_masked_settings(body, state.settings)  # 掩码保持原密码
    persist_settings(merged, settings_path())
    state.settings = merged
    state.pusher.notice(
        "info", "设置已保存。MQTT/CTP 连接参数将在重启应用后生效"
    )
    return Ok()


@commands.command()
async def list_strategies(body: Empty, state: Annotated[AppState, State()]) -> StrategyListResp:
    return StrategyListResp([s.model_dump(mode="json") for s in state.store.list()])


@commands.command()
async def create_strategy(body: CreateBody, state: Annotated[AppState, State()]) -> StrategyListResp:
    def run():
        from faos_app.log import log

        log(f"FAOS_CREATE strategy mode={body.mode}")
        if body.mode == "pair":
            if not body.leg_a or not body.leg_b:
                raise ValueError("请填写 A、B 两腿合约号")
            state.manager.create_pair(body.leg_a, body.leg_b, body.single_lots, body.total_lots)
        elif body.mode == "product":
            if not body.product:
                raise ValueError("请填写品种代码")
            state.manager.create_product(body.product, body.single_lots, body.total_lots)
        else:
            raise ValueError(f"未知创建方式: {body.mode}")
        return StrategyListResp([s.model_dump(mode="json") for s in state.store.list()])

    return _guard(state, run)


@commands.command()
async def subscribe_strategy(body: IdBody, state: Annotated[AppState, State()]) -> Strategy:
    return _guard(state, lambda: state.manager.subscribe(body.strategy_id))


@commands.command()
async def delete_strategy(body: IdBody, state: Annotated[AppState, State()]) -> StrategyListResp:
    return _guard(state, lambda: StrategyListResp(
        [s.model_dump(mode="json") for s in state.manager.delete(body.strategy_id)]))


class ProductBody(BaseModel):
    product: str


@commands.command()
async def delete_product(body: ProductBody, state: Annotated[AppState, State()]) -> StrategyListResp:
    """品种退订：删除该品种全部策略并批量退订行情。"""
    return _guard(state, lambda: StrategyListResp(
        [s.model_dump(mode="json") for s in state.manager.delete_product(body.product)]))


@commands.command()
async def stop_strategy(body: IdBody, state: Annotated[AppState, State()]) -> Strategy:
    return _guard(state, lambda: state.manager.stop(body.strategy_id))


@commands.command()
async def set_lots(body: SetLotsBody, state: Annotated[AppState, State()]) -> Strategy:
    return _guard(state, lambda: state.manager.set_lots(
        body.strategy_id, body.single_lots, body.total_lots))


@commands.command()
async def place_trigger(body: PlaceTriggerBody, state: Annotated[AppState, State()]) -> "object":
    def run():
        try:
            action = TriggerAction(body.action)
        except ValueError:
            raise ValueError(f"未知触发动作: {body.action}")
        return state.manager.place_trigger(body.strategy_id, action, body.spread, body.lots)

    return _guard(state, run)


@commands.command()
async def cancel_trigger(body: CancelTriggerBody, state: Annotated[AppState, State()]) -> Strategy:
    return _guard(state, lambda: state.manager.cancel_trigger(body.strategy_id, body.trigger_id))


@commands.command()
async def get_leg_orders(body: IdBody, state: Annotated[AppState, State()]) -> DictResp:
    if state.leg_ops is None:
        return DictResp({"leg_a": {}, "leg_b": {}})
    return _guard(state, lambda: DictResp(state.leg_ops.leg_snapshot(body.strategy_id)))


@commands.command()
async def cancel_leg_orders(body: LegBody, state: Annotated[AppState, State()]) -> CancelLegResp:
    if state.leg_ops is None:
        return CancelLegResp(cancelled=0)
    return _guard(state, lambda: CancelLegResp(
        cancelled=state.leg_ops.cancel_leg(body.strategy_id, body.leg)))


@commands.command()
async def get_app_status(body: Empty, state: Annotated[AppState, State()]) -> DictResp:
    return DictResp(state.app_status())


@commands.command()
async def login(body: LoginBody, state: Annotated[AppState, State()]) -> LoginResp:
    """登录：派生 MQTT 凭据（u+CTP账号），连接 MQTT；offline=False 时同时连 CTP。"""
    apply_login(state.settings, body.ctp_account, body.ctp_password,
                body.counter, body.mqtt_host, body.broker,
                mqtt_port=body.mqtt_port, ctp_td=body.ctp_td, ctp_md=body.ctp_md)
    if body.remember:
        persist_settings(state.settings, settings_path())  # 记住我：凭据落盘
    state.feed.start()  # 幂等
    if not body.offline:
        if state.ctp is None:
            raise ValueError("CTP 服务初始化失败，请查看日志")
        state.ctp.connect(state.settings.ctp)
    state.pusher.notice("info", "离线登录：仅行情连接" if body.offline else "登录成功：行情与交易连接中")
    return LoginResp(ok=True, offline=body.offline)


@commands.command()
async def get_login_state(body: Empty, state: Annotated[AppState, State()]) -> LoginStateResp:
    """登录页预填：记住我的凭据（remembered 标记是否来自已保存配置）。"""
    from faos_app.config import MASK

    s = state.settings
    remembered = bool(s.ctp.用户名)
    return LoginStateResp(
        ctp_account=s.ctp.用户名,
        ctp_password=s.ctp.密码,
        counter=s.ctp.柜台环境,
        mqtt_host=s.mqtt.host,
        mqtt_port=s.mqtt.port,
        ctp_td=s.ctp.交易服务器,
        ctp_md=s.ctp.行情服务器,
        broker=s.ctp.经纪商代码,
        remembered=remembered,
    )


@commands.command()
async def set_strategy_config(body: SetConfigBody, state: Annotated[AppState, State()]) -> Strategy:
    return _guard(state, lambda: state.manager.set_config(
        body.strategy_id, body.slip_a, body.slip_b, body.order_type_a, body.order_type_b))


@commands.command()
async def stop_strategy_direction(body: StopDirectionBody, state: Annotated[AppState, State()]) -> Strategy:
    return _guard(state, lambda: state.manager.stop_direction(body.strategy_id, body.side))


@commands.command()
async def connect_mqtt(body: Empty, state: Annotated[AppState, State()]) -> Ok:
    state.feed.start()  # 幂等；连接成功/失败经 py://conn-state 推送
    return Ok()


@commands.command()
async def disconnect_mqtt(body: Empty, state: Annotated[AppState, State()]) -> Ok:
    state.feed.stop()
    state.pusher.conn_state("mqtt", "disconnected")
    return Ok()


@commands.command()
async def connect_ctp(body: Empty, state: Annotated[AppState, State()]) -> Ok:
    if state.ctp is None:
        raise ValueError("CTP 服务初始化失败，请查看日志")
    state.ctp.connect(state.settings.ctp)  # SimNow 仅交易时段可登录；结果经 py://conn-state
    return Ok()
