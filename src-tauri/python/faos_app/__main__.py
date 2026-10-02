"""python -m faos_app 的入口。"""

import sys
from multiprocessing import freeze_support

# PyInstaller/pyo3 standalone 打包下，Windows spawn 子进程会重新执行入口模块；
# 不调用 freeze_support 会造成无限 spawn 或重复启动。
freeze_support()

from faos_app.main import main

sys.exit(main())
