"""python -m faos_app 的入口。"""

import sys
from multiprocessing import freeze_support

# PyInstaller/pyo3 standalone 打包下，Windows spawn 子进程会重新执行入口模块；
# 不调用 freeze_support 会造成无限 spawn 或重复启动。
freeze_support()

# GUI 子系统（双击运行）无控制台 → stdout/stderr 为 None，vnpy 导入即崩：
# 必须在导入 faos_app.main（间接导入 vnpy）之前补空接收器
from faos_app.log import ensure_stdio

ensure_stdio()

# 单实例锁（2026-10-15 定稿）：双进程会互踢 SimNow 会话（登录卡结算确认的主因候选）；
# 互斥体为内核对象，进程退出（含崩溃）自动释放，无残留死锁
from faos_app.singleinstance import acquire, notify_already_running

if not acquire():
    from faos_app.log import log

    log("FAOS_ALREADY_RUNNING 二次启动被拒绝（单实例锁）")
    notify_already_running()
    sys.exit(1)

from faos_app.main import main

sys.exit(main())
