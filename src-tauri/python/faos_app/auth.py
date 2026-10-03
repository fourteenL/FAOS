"""登录凭据派生：MQTT 账号 = u + CTP 账号，MQTT 密码 = CTP 密码。"""

from faos_app.config import Settings


def apply_login(settings: Settings, ctp_account: str, ctp_password: str,
                counter: str, mqtt_host: str) -> None:
    account = ctp_account.strip()
    settings.ctp.用户名 = account
    settings.ctp.密码 = ctp_password
    settings.ctp.柜台环境 = counter if counter in ("实盘", "测试") else "测试"
    settings.mqtt.host = mqtt_host.strip()
    settings.mqtt.user = f"u{account}"
    settings.mqtt.password = ctp_password
