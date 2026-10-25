"""网络检测：柜台环境前置延迟（TCP 握手毫秒）。

独立于 commands（pytauri 边界，pytest 无法导入）——分层后可单测。
"""

import socket
import time


def tcp_latency_ms(addr: str, timeout: float = 2.0) -> int:
    """TCP 握手延迟（毫秒）；地址无效/超时/拒绝返回 -1（柜台环境健康检测用）。"""
    if not addr or "://" not in addr:
        return -1
    host_port = addr.split("://", 1)[1]
    host, _, port = host_port.rpartition(":")
    if not host or not port.isdigit():
        return -1
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return int((time.monotonic() - t0) * 1000)
    except Exception:
        return -1
