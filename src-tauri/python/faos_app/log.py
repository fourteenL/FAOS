"""诊断日志：按天分文件（logs/faos-YYYY-MM-DD.log）+ 保留期自动清理。

- 行情不入日志（高频写盘曾是性能根因之一）
- 跨天写入自动轮转到新文件
- cleanup_old_logs 按保留天数删除过期日志文件（启动时与跨天轮转时触发）
"""

import datetime
import os
import re
import sys
import time
from pathlib import Path

_LOG_FILE = None
_LOG_DATE = None
_RETENTION_DAYS = 15
_NAME_RE = re.compile(r"^faos-(\d{4})-(\d{2})-(\d{2})\.log$")


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


def _today() -> datetime.date:
    return datetime.date.today()


def _log_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / "faos" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def configure(days: int = 15) -> None:
    """设置保留天数（设置面板「日志」项；main.py 启动时注入）。"""
    global _RETENTION_DAYS
    _RETENTION_DAYS = max(1, int(days or 15))


def cleanup_old_logs(directory: Path = None, days: int = None,
                     today: datetime.date = None) -> int:
    """删除超过保留期的日志文件，返回删除数。按文件名日期判断。"""
    directory = directory if directory is not None else _log_dir()
    days = days if days is not None else _RETENTION_DAYS
    today = today if today is not None else _today()
    cutoff = today - datetime.timedelta(days=days)
    removed = 0
    try:
        for f in directory.iterdir():
            m = _NAME_RE.match(f.name)
            if not m or not f.is_file():
                continue
            try:
                file_date = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
            if file_date < cutoff:
                f.unlink(missing_ok=True)
                removed += 1
    except OSError:
        pass
    return removed


def _file_for(date: datetime.date) -> Path:
    return _log_dir() / f"faos-{date.isoformat()}.log"


def log(msg: str) -> None:
    global _LOG_FILE, _LOG_DATE
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    today = _today()
    try:
        if _LOG_FILE is None or _LOG_DATE != today:
            if _LOG_FILE is not None:
                _LOG_FILE.close()
            _LOG_DATE = today
            _LOG_FILE = _file_for(today).open("a", encoding="utf-8")
            cleanup_old_logs(today=today)  # 跨天轮转时顺带清理过期文件
        _LOG_FILE.write(line + "\n")
        _LOG_FILE.flush()
    except Exception:
        pass


def app_version() -> str:
    """当前应用版本（_version.py；打包时被构建脚本覆写为发版号）。"""
    try:
        from faos_app import _version

        return _version.__version__
    except Exception:
        return "unknown"


def banner() -> None:
    log(f"FAOS 启动 v{app_version()} python={sys.version.split()[0]} argv={sys.argv[:2]}")


def _reset_for_test() -> None:
    global _LOG_FILE, _LOG_DATE
    if _LOG_FILE is not None:
        try:
            _LOG_FILE.close()
        except Exception:
            pass
    _LOG_FILE = None
    _LOG_DATE = None
