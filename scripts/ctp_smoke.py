"""SimNow CTP 登录冒烟脚本（交易时段手动运行）。

用法：.venv/Scripts/python scripts/ctp_smoke.py
验证：登录 9999 → 查持仓 → 订阅 ag 行情 → 打印 tick → Ctrl+C 退出。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src-tauri" / "python"))

from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.event import EVENT_LOG, EVENT_TICK
from vnpy.trader.object import SubscribeRequest

from faos_app.config import load_effective_settings, settings_path
from faos_app.ctp import build_ctp_setting


def main() -> int:
    from vnpy_ctp.gateway.ctp_gateway import CtpGateway

    settings = load_effective_settings(settings_path())
    ee = EventEngine()
    me = MainEngine(ee)
    ee.start()

    ee.register(EVENT_LOG, lambda e: print("[LOG]", e.data.msg))
    ee.register(EVENT_TICK, lambda e: print(
        "[TICK]", e.data.vt_symbol, e.data.last_price, e.data.bid_price_1, e.data.ask_price_1))

    me.add_gateway(CtpGateway)
    me.connect(build_ctp_setting(settings.ctp), "CTP")

    time.sleep(8)
    req = SubscribeRequest(symbol="ag2606", exchange=__import__("vnpy.trader.constant", fromlist=["Exchange"]).Exchange.SHFE)
    me.subscribe(req, "CTP")

    print("运行 30 秒，观察持仓/行情……")
    time.sleep(30)
    oms = me.get_engine("oms") or getattr(me, "oms", None)
    if oms:
        for p in oms.get_all_positions():
            print("[POS]", p.vt_symbol, p.direction.value, p.volume)
    me.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
