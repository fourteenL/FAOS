"""诊断日志：stdout + %APPDATA%/faos/faos.log 双写。

release 构建为 windows 子系统（无控制台），文件日志是打包后唯一可见输出。
"""

import os
import sys
import time
from pathlib import Path

_LOG_FILE = None


class NullIO:
    """GUI 子系统（无控制台）下顶替 None stdio 的空接收器：可写、可 flush、不积累。"""

    def write(self, *args) -> int:
        return 0

    def flush(self) -> None:
        pass


def ensure_stdio() -> None:
    """无控制台环境把 None 的 stdout/stderr 换成空接收器。

    vnpy.trader.logger 在 import 时 logger.add(sink=sys.stdout)，stdout 为 None
    会抛 loguru TypeError——必须在导入 faos_app.main（间接导入 vnpy）之前调用。
    """
    if sys.stdout is None:
        sys.stdout = NullIO()  # type: ignore[assignment]
    if sys.stderr is None:
        sys.stderr = NullIO()  # type: ignore[assignment]


def _log_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / "faos"
    d.mkdir(parents=True, exist_ok=True)
    return d / "faos.log"


def log(msg: str) -> None:
    global _LOG_FILE
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        if _LOG_FILE is None:
            _LOG_FILE = _log_path().open("a", encoding="utf-8")
        _LOG_FILE.write(line + "\n")
        _LOG_FILE.flush()
    except Exception:
        pass


def banner() -> None:
    log(f"FAOS 启动 python={sys.version.split()[0]} argv={sys.argv[:2]}")
