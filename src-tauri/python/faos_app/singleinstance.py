"""单实例锁（Windows 命名互斥体）：防止重复启动互踢 SimNow 会话。

互斥体是内核对象，进程退出（含崩溃）即自动释放——无文件锁的残留死锁问题。
2026-10-15 用户定稿：双进程启动会互踢 SimNow 会话（当天频繁卡④主因候选）。
"""

from typing import Optional

_MUTEX_NAME = "FAOS_SingleInstance_Lock"
_ERROR_ALREADY_EXISTS = 183  # win32 GetLastError()

# 持有互斥体句柄（防 GC；进程存活期间保持占用）
_held: Optional[int] = None


def acquire() -> bool:
    """尝试取得单实例锁；已存在实例（含本进程重复获取）返回 False。"""
    global _held
    import ctypes

    handle = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    already = ctypes.GetLastError() == _ERROR_ALREADY_EXISTS
    if already:
        return False
    _held = handle  # 首个实例持有
    return True


def notify_already_running() -> None:
    """弹原生消息框告知二次启动（GUI 子系统无控制台，必须用 MessageBox）。"""
    import ctypes

    MB_ICONWARNING = 0x30
    ctypes.windll.user32.MessageBoxW(
        None,
        "FAOS 已在运行，本次启动已退出。\n\n"
        "重复启动会互踢 SimNow 行情/交易会话（另一台电脑同理），"
        "导致登录卡在结算确认。",
        "FAOS 重复启动",
        MB_ICONWARNING,
    )
