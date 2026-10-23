"""应用版本号（启动日志/诊断用）。

开发态默认 dev；打包脚本（build_release.ps1）在 pip 安装后把 pyembed 内
本文件覆写为实际发版号（如 0.1.0-beta.22）——log 启动横幅会带版本，
便于用户拷来的 faos-*.log 直接对上安装包版本。
"""

__version__ = "0.1.0-dev"
