"""配置读写：settings.json（CTP / 算法参数）。

安全约定：源代码零密钥。账号/密码/服务器等敏感配置放在
`config/settings.local.json`（git 忽略，结构见 config/settings.example.json），
打包分发时放在 exe 同目录 config/ 下。加载顺序（后层覆盖前层）：

1. 代码内置结构性默认值（不含任何密钥）
2. 仓库/exe 同目录的 config/settings.local.json
3. %APPDATA%/faos/settings.json（界面「设置」写入的运行时覆盖层）

CTP 字段名与 vnpy_ctp 6.7.11 网关 default_setting 的中文键一致。
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

import pydantic
from pydantic import BaseModel


class CtpEnvironment(BaseModel):
    """柜台环境预设（登录页动态下拉；用户可增删）。"""

    name: str
    mode: str = "实盘"  # "实盘" | "测试"（vnpy_ctp 柜台环境，生产/测试证书）
    td: str = ""  # 交易前置
    md: str = ""  # 行情前置


DEFAULT_CTP_ENVIRONMENTS = [
    CtpEnvironment(name="SimNow 第一组", mode="实盘",
                   td="tcp://182.254.243.31:30001", md="tcp://182.254.243.31:30011"),
    CtpEnvironment(name="SimNow 第二组", mode="实盘",
                   td="tcp://182.254.243.31:30002", md="tcp://182.254.243.31:30012"),
    CtpEnvironment(name="SimNow 第三组", mode="实盘",
                   td="tcp://182.254.243.31:30003", md="tcp://182.254.243.31:30013"),
    CtpEnvironment(name="SimNow 7×24", mode="实盘",
                   td="tcp://182.254.243.31:40001", md="tcp://182.254.243.31:40011"),
]


def load_ctp_environments(extra_dirs=None) -> list:
    """柜台环境表：config/ctp_environments.json（可手工编辑）> 内置默认（2026-10-24
    用户定稿：登录页的环境列表落到 config 目录，随仓库/安装包分发）。

    候选：开发态=模块位置向上找 config/；打包态=exe 同目录 config/；
    extra_dirs 显式指定优先；APPDATA 兜底（用户手工放置）。界面上的增删
    仍写 %APPDATA%/faos/settings.json 的 ctp_environments 覆盖层。"""
    import json as _json
    import sys as _sys
    from pathlib import Path as _Path

    candidates = []
    if extra_dirs:
        candidates += [_Path(d) / "ctp_environments.json" for d in extra_dirs]
    try:
        here = _Path(__file__).resolve()
        for parent in here.parents[:6]:
            candidates.append(parent / "config" / "ctp_environments.json")
    except Exception:
        pass
    try:
        candidates.append(_Path(_sys.executable).resolve().parent / "config" / "ctp_environments.json")
    except Exception:
        pass
    candidates.append(default_data_dir() / "ctp_environments.json")
    for f in candidates:
        try:
            if f.exists():
                data = _json.loads(f.read_text(encoding="utf-8"))
                envs = data.get("environments") if isinstance(data, dict) else data
                if isinstance(envs, list) and envs:
                    return [CtpEnvironment.model_validate(e) for e in envs]
        except Exception:
            continue
    return list(DEFAULT_CTP_ENVIRONMENTS)


class CtpSettings(BaseModel):
    # 中文键 = vnpy_ctp 网关 default_setting 键名
    用户名: str = ""
    密码: str = ""
    经纪商代码: str = ""
    交易服务器: str = ""
    行情服务器: str = ""
    产品名称: str = ""
    授权编码: str = ""
    柜台环境: str = "实盘"  # SimNow 新前置要求生产证书；「测试」会 4097 握手失败


class AlgoSettings(BaseModel):
    payup: int = 5  # 追价跳数上限
    interval: int = 3  # 算法轮询间隔（秒）
    timeout: int = 300  # 算法看门狗超时（秒，超时停止并告警）
    close_risk_seconds: int = 60  # 收盘前风控提前量（秒；0=关闭风控）
    # lock/close_risk_breaks 已退役（2026-10-24）：开平锁仓语义固定、时段表全时点
    # 触发；旧配置文件里的多余字段被 pydantic 忽略，无需迁移
    stale_seconds: int = 30  # 行情停滞阈值（秒）：任一腿超时即不合成不触发，面板显示各腿停滞秒数
    # 交易时段表（config/market_sessions.json；每段 start/end，end 可为 "次日 H:MM"）
    # 风控=各时段 end 前 N 秒触发。用户在设置抽屉保存后覆盖本字段。
    market_sessions: list[dict] = []  # [{"name":..., "start":"09:00","end":"10:15"}]


class LogSettings(BaseModel):
    days: int = 15  # 日志保留天数（按天分文件，过期自动清理）


SHFE_PRODUCTS_DEFAULT = [
    "ad", "ag", "al", "ao", "au", "br", "bu", "cu", "fu", "hc",
    "ni", "op", "pb", "rb", "ru", "sn", "sp", "ss", "wr", "zn",
]


# 上期所品种最小变动价位（元）：差价输入框的步进/精度依据；配置文件可修正
PRODUCT_TICKS_DEFAULT = {
    "ad": 1, "ag": 1, "al": 5, "ao": 1, "au": 0.02, "br": 5, "bu": 2,
    "cu": 10, "fu": 1, "hc": 1, "ni": 10, "op": 1, "pb": 5, "rb": 1,
    "ru": 5, "sn": 10, "sp": 2, "ss": 5, "wr": 1, "zn": 5,
}


class Settings(BaseModel):
    ctp: CtpSettings = CtpSettings()
    algo: AlgoSettings = AlgoSettings()
    log: LogSettings = LogSettings()
    products: list[str] = SHFE_PRODUCTS_DEFAULT  # 表格上方品种筛选列表，配置文件可增删
    product_dominant: dict[str, str] = {}  # 品种主力合约配置覆盖（如 {"ag": "2612"}；缺省=最近未来月自动推断）
    # 柜台环境列表（登录页可增删）：config/ctp_environments.json > 内置默认
    ctp_environments: list[CtpEnvironment] = pydantic.Field(
        default_factory=load_ctp_environments)
    product_ticks: dict[str, float] = PRODUCT_TICKS_DEFAULT  # 品种 -> 最小变动价位


MASK = "******"  # 设置回显掩码；保存时等于该值表示「保持原密码」


def masked_settings(s: Settings) -> Settings:
    """回显用副本：密码字段替换为掩码。"""
    m = s.model_copy(deep=True)
    m.ctp.密码 = MASK
    return m


def merge_masked_settings(incoming: Settings, current: Settings) -> Settings:
    """保存合并：掩码密码回落为当前值，其余以 incoming 为准。"""
    merged = incoming.model_copy(deep=True)
    if merged.ctp.密码 == MASK:
        merged.ctp.密码 = current.ctp.密码
    return merged


def default_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / "faos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_path() -> Path:
    return default_data_dir() / "settings.json"


def load_settings(path: Path) -> Settings:
    if not path.exists():
        return Settings()
    return Settings.model_validate_json(path.read_text(encoding="utf-8"))


REPO_CONFIG_RELPATH = Path("config") / "settings.local.json"


def find_repo_config() -> Optional[Path]:
    """定位 git 忽略的本地配置文件（不抛错，找不到返回 None）。

    候选：① 从本模块位置向上逐级查找（开发态 = 仓库根）；
    ② 打包态 = faos.exe 同目录的 config/settings.local.json。
    """
    candidates = []
    try:
        here = Path(__file__).resolve()
        for parent in here.parents[:6]:
            candidates.append(parent / REPO_CONFIG_RELPATH)
    except Exception:
        pass
    try:
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / REPO_CONFIG_RELPATH)
    except Exception:
        pass
    for c in candidates:
        if c.is_file():
            return c
    return None


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_effective_settings(appdata_path: Path, repo_path: Optional[Path] = None) -> Settings:
    """三层合并加载：内置默认 → repo/exe 本地配置 → APPDATA 运行时设置。"""
    data: dict = {}
    if repo_path is None:
        repo_path = find_repo_config()
    for layer in (repo_path, appdata_path):
        if layer is not None and Path(layer).exists():
            try:
                layer_data = json.loads(Path(layer).read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(layer_data, dict):
                data = _deep_merge(data, layer_data)
    return Settings.model_validate(data)


def save_settings(settings: Settings, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, path)
