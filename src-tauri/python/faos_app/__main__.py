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

from faos_app.main import main

sys.exit(main())
