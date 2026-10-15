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
    companion_spread: Optional[float] = None  # 开仓下单时对应的平仓价一并保存
    replace_trigger_id: Optional[str] = None  # 修改挂单价：旧单稍后撤，净敞口按差值计


class CancelTriggerBody(IdBody):
    trigger_id: str


class LegBody(IdBody):
    leg: str  # "A" | "B"


class ReorderBody(BaseModel):
    ordered_ids: list[int]  # 拖动后的完整顺序（按此重排 sort_key）


class CloseLegTargetBody(LegBody):
    long_today: Optional[float] = None
    long_yd: Optional[float] = None
    short_today: Optional[float] = None
    short_yd: Optional[float] = None  # 今昨仓管理：目标持仓（未填项不动，只平不开）


class LoginBody(BaseModel):
    ctp_account: str
    ctp_password: str
    counter: str = "实盘"  # SimNow 新前置要求生产证书（实盘）；「测试」会 4097 握手失败
    ctp_td: str = ""  # 交易前置（空=不改配置）
    ctp_md: str = ""  # 行情前置（空=不改配置）
    broker: str = "9999"
    remember: bool = False


class LoginResp(BaseModel):
    ok: bool = True


class LoginStateResp(BaseModel):
    ctp_account: str = ""
    ctp_password: str = ""
    counter: str = ""
    ctp_td: str = ""
    ctp_md: str = ""
    broker: str = "9999"
    remembered: bool = False
    environments: list = []


class SetConfigBody(IdBody):
    slip_a: Optional[int] = None
    max_cancels_a: Optional[int] = None  # A 腿撤单上限（风控）
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
async def get_settings(body: Empty, state: Annotated[AppState, State()]) -> Settings:
    return masked_settings(state.settings)  # 密码回显掩码


class ListEnvResp(BaseModel):
    class EnvItem(BaseModel):
        name: str
        mode: str
        td: str
        md: str

    environments: list["ListEnvResp.EnvItem"] = []


class PasswordsResp(BaseModel):
    ctp: str = ""


class EnvBody(BaseModel):
    name: str
    mode: str = "实盘"
    td: str = ""
    md: str = ""


class EnvNameBody(BaseModel):
    name: str


@commands.command()
async def add_environment(body: EnvBody, state: Annotated[AppState, State()]) -> ListEnvResp:
    """添加柜台环境（同名覆盖更新）。"""
    name = body.name.strip()
    if not name:
        raise ValueError("环境名称不能为空")
    if body.mode not in ("实盘", "测试"):
        raise ValueError("柜台模式应为 实盘/测试")
    envs = [e for e in state.settings.ctp_environments if e.name != name]
    from faos_app.config import CtpEnvironment

    envs.append(CtpEnvironment(name=name, mode=body.mode, td=body.td.strip(), md=body.md.strip()))
    state.settings.ctp_environments = envs
    persist_settings(state.settings, settings_path())  # 环境列表落盘
    return ListEnvResp(environments=[e.model_dump() for e in envs])


@commands.command()
async def remove_environment(body: EnvNameBody, state: Annotated[AppState, State()]) -> ListEnvResp:
    """删除柜台环境（至少保留一个）。"""
    envs = [e for e in state.settings.ctp_environments if e.name != body.name.strip()]
    if len(envs) == len(state.settings.ctp_environments):
        raise ValueError(f"环境不存在: {body.name}")
    if not envs:
        raise ValueError("至少保留一个柜台环境")
    state.settings.ctp_environments = envs
    persist_settings(state.settings, settings_path())
    return ListEnvResp(environments=[e.model_dump() for e in envs])


@commands.command()
async def get_passwords(body: Empty, state: Annotated[AppState, State()]) -> PasswordsResp:
    """设置面板「显示密码」：返回明文（本机自用，用户明确要求可查看原文）。"""
    return PasswordsResp(ctp=state.settings.ctp.密码)


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
    async def warm_dominant(product: str) -> None:
        """品种订阅前先订全合约行情，等首批持仓量实测市场主力（最多 4 秒，非交易时段跳过）。"""
        import anyio

        matched = state.manager.subscribe_product_contracts(product)
        if not matched:
            return
        prefix = f"{product.strip().lower()}"
        deadline = 4.0
        waited = 0.0
        while waited < deadline:
            oi = {vt: v for vt, v in state.ctp.get_open_interest().items()
                  if vt.split(".")[0].rstrip("0123456789") == prefix
                  or "".join(ch for ch in vt.split(".")[0] if ch.isalpha()) == prefix}
            if len(oi) >= min(2, len(matched)):
                state.manager.set_dominant_hint(prefix, oi)
                return
            await anyio.sleep(0.4)
            waited += 0.4

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

    if body.mode == "product":
        await warm_dominant(body.product)
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
async def stop_all(body: Empty, state: Annotated[AppState, State()]) -> Ok:
    """一键停止全部策略：撤所有触发单+停所有算法（持仓保留）。"""
    stopped = [s.id for s in state.manager.stop_all()]
    state.pusher.notice("success", f"已停止全部 {len(stopped)} 个策略（撤触发单+停算法），持仓保留")
    return Ok()


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
        return state.manager.place_trigger(body.strategy_id, action, body.spread, body.lots,
                                            companion_spread=body.companion_spread,
                                            replace_trigger_id=body.replace_trigger_id)

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
async def close_leg_target(body: CloseLegTargetBody, state: Annotated[AppState, State()]) -> DictResp:
    if state.leg_ops is None:
        raise ValueError("CTP 未初始化")
    return _guard(state, lambda: DictResp(state.leg_ops.close_leg_to_target(
        body.strategy_id, body.leg,
        {"long_today": body.long_today, "long_yd": body.long_yd,
         "short_today": body.short_today, "short_yd": body.short_yd})))


@commands.command()
async def get_app_status(body: Empty, state: Annotated[AppState, State()]) -> DictResp:
    return DictResp(state.app_status())


@commands.command()
async def login(body: LoginBody, state: Annotated[AppState, State()]) -> LoginResp:
    """登录：写 CTP 凭据并连接（行情链路已退役，无 MQTT）。"""
    apply_login(state.settings, body.ctp_account, body.ctp_password,
                body.counter, body.broker,
                ctp_td=body.ctp_td, ctp_md=body.ctp_md)
    if body.remember:
        persist_settings(state.settings, settings_path())  # 记住我：凭据落盘
    if state.ctp is None:
        raise ValueError("CTP 服务初始化失败，请查看日志")
    state.ctp.connect(state.settings.ctp)  # SimNow 仅交易时段可登录；状态经 py://conn-state 推送
    n = state.manager.resubscribe_all()  # 登录即重订行情（网关缓冲随登录生效）
    state.pusher.notice("info", f"登录中：正在连接 CTP（合约加载约 1~3 分钟，就绪会提醒）；已重订 {n} 个合约行情")
    return LoginResp(ok=True)


@commands.command()
async def get_login_state(body: Empty, state: Annotated[AppState, State()]) -> LoginStateResp:
    """登录页预填：记住我的凭据（remembered 标记是否来自已保存配置）。"""
    from faos_app.config import MASK

    s = state.settings
    remembered = bool(s.ctp.用户名)
    return LoginStateResp(
        ctp_account=s.ctp.用户名,
        ctp_password=s.ctp.密码,
        counter=next((e.name for e in s.ctp_environments if e.mode == s.ctp.柜台环境
                      and e.td == s.ctp.交易服务器), s.ctp_environments[0].name),
        ctp_td=s.ctp.交易服务器,
        ctp_md=s.ctp.行情服务器,
        broker=s.ctp.经纪商代码,
        remembered=remembered,
        environments=[e.model_dump() for e in s.ctp_environments],
    )


@commands.command()
async def set_strategy_config(body: SetConfigBody, state: Annotated[AppState, State()]) -> Strategy:
    return _guard(state, lambda: state.manager.set_config(
        body.strategy_id, body.slip_a, body.slip_b, body.order_type_a, body.order_type_b,
        max_cancels_a=body.max_cancels_a))


@commands.command()
async def stop_strategy_direction(body: StopDirectionBody, state: Annotated[AppState, State()]) -> Strategy:
    return _guard(state, lambda: state.manager.stop_direction(body.strategy_id, body.side))


@commands.command()
async def reorder_strategies(body: ReorderBody, state: Annotated[AppState, State()]) -> StrategyListResp:
    return _guard(state, lambda: StrategyListResp(
        [s.model_dump(mode="json") for s in state.manager.reorder_strategies(body.ordered_ids)]))


@commands.command()
async def connect_ctp(body: Empty, state: Annotated[AppState, State()]) -> Ok:
    if state.ctp is None:
        raise ValueError("CTP 服务初始化失败，请查看日志")
    state.ctp.connect(state.settings.ctp)  # SimNow 仅交易时段可登录；结果经 py://conn-state
    return Ok()
