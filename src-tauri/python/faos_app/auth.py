"""登录凭据派生：MQTT 账号 = u + CTP 账号，MQTT 密码 = CTP 密码。"""

from typing import Optional

from faos_app.config import Settings


def apply_login(settings: Settings, ctp_account: str, ctp_password: str,
                counter: str, mqtt_host: str, broker: str = "9999",
                mqtt_port: Optional[int] = None,
                ctp_td: str = "", ctp_md: str = "") -> None:
    account = ctp_account.strip()
    settings.ctp.用户名 = account
    settings.ctp.密码 = ctp_password
    settings.ctp.经纪商代码 = broker.strip() or "9999"
    settings.ctp.柜台环境 = counter if counter in ("实盘", "测试") else "实盘"
    settings.mqtt.host = mqtt_host.strip()
    if mqtt_port and 1 <= int(mqtt_port) <= 65535:
        settings.mqtt.port = int(mqtt_port)
    if ctp_td.strip():
        settings.ctp.交易服务器 = ctp_td.strip()
    if ctp_md.strip():
        settings.ctp.行情服务器 = ctp_md.strip()
    settings.mqtt.user = f"u{account}"
    settings.mqtt.password = ctp_password
