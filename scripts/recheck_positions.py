"""复核账户持仓（登录→快照→退出）：验证验收脚本结束后的账户最终状态。"""
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, str(Path(__file__).parent.parent / "src-tauri" / "python"))

from anyio.from_thread import start_blocking_portal

from faos_app.config import load_effective_settings, settings_path
from faos_app.ctp import CtpService
from faos_app.state import AppState
from faos_app.store import StrategyStore


def wait_for(pred, timeout, what):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.3)
    print(f"[TIMEOUT] {what}")
    return False


class Push:
    def __getattr__(self, name):
        def _noop(*a, **k):
            pass
        return _noop


with start_blocking_portal("asyncio") as portal:
    settings = load_effective_settings(settings_path())
    store = StrategyStore()
    ctp = CtpService(push=Push())
    ctp.start(settings.ctp, portal, connect=False)
    ctp.connect(settings.ctp)
    if not wait_for(lambda: ctp.state == "ready", 600, "CTP 就绪"):
        sys.exit(2)
    ctp.get_positions()  # 触发一轮查询
    time.sleep(6)  # 等持仓回报落缓存
    snap = {}
    for p in ctp.get_positions():
        v = getattr(p.direction, "value", str(p.direction))
        d = {"多": "long", "空": "short"}.get(v, str(v).lower())
        if p.volume:
            snap[(p.vt_symbol, d)] = snap.get((p.vt_symbol, d), 0) + p.volume
    print("ACCOUNT_POSITIONS:", snap or "（全平）")
    import os

    os._exit(0)
