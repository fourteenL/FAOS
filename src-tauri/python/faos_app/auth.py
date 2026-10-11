"""登录：CTP 凭据写入设置（行情链路退役，无 MQTT 派生）。"""

from faos_app.config import Settings


def apply_login(settings: Settings, ctp_account: str, ctp_password: str,
                counter: str, broker: str = "9999",
                ctp_td: str = "", ctp_md: str = "") -> None:
    """counter=环境名（settings.ctp_environments 查询）；未知名保持现有服务器、模式兜底实盘。"""
    settings.ctp.用户名 = ctp_account.strip()
    settings.ctp.密码 = ctp_password
    settings.ctp.经纪商代码 = broker.strip() or "9999"
    env = next((e for e in settings.ctp_environments if e.name == counter), None)
    if env is not None:
        settings.ctp.柜台环境 = env.mode
        settings.ctp.交易服务器 = env.td
        settings.ctp.行情服务器 = env.md
    else:
        settings.ctp.柜台环境 = "实盘"
    if ctp_td.strip():  # 显式传入优先（兼容）
        settings.ctp.交易服务器 = ctp_td.strip()
    if ctp_md.strip():
        settings.ctp.行情服务器 = ctp_md.strip()
