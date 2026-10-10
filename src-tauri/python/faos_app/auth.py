"""登录：CTP 凭据写入设置（行情链路退役，无 MQTT 派生）。"""

from faos_app.config import Settings


def apply_login(settings: Settings, ctp_account: str, ctp_password: str,
                counter: str, broker: str = "9999",
                ctp_td: str = "", ctp_md: str = "") -> None:
    settings.ctp.用户名 = ctp_account.strip()
    settings.ctp.密码 = ctp_password
    settings.ctp.经纪商代码 = broker.strip() or "9999"
    settings.ctp.柜台环境 = counter if counter in ("实盘", "测试") else "实盘"
    if ctp_td.strip():
        settings.ctp.交易服务器 = ctp_td.strip()
    if ctp_md.strip():
        settings.ctp.行情服务器 = ctp_md.strip()
