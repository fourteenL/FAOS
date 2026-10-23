# FAOS standalone 发布构建脚本
# 产物：src-tauri/target/bundle-release/bundle/nsis/FAOS_x.y.z_x64-setup.exe（及 msi）
# 前置：rustup(msvc)、node/npm 已安装；本脚本自动准备嵌入式 Python。
# 版本命名：默认生成 beta 包（0.1.0-beta.N 自增，扫现有包取最大号+1）；
#          -Release 参数生成正式包（0.1.0，无 beta 后缀）。
param([switch]$Release)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$pyembed = Join-Path $repo "src-tauri\pyembed\python"
$pbsTag = "20260929"
$pbsUrl = "https://github.com/astral-sh/python-build-standalone/releases/download/$pbsTag/cpython-3.13.15%2B$pbsTag-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"

# 1. 嵌入式 Python（存在即跳过）
if (-not (Test-Path "$pyembed\python.exe")) {
    Write-Host "[1/4] 下载 python-build-standalone 3.13.15 ..."
    $tgz = Join-Path $env:TEMP "pbs-faos.tar.gz"
    Invoke-WebRequest -Uri $pbsUrl -OutFile $tgz
    New-Item -ItemType Directory -Force -Path (Join-Path $repo "src-tauri\pyembed") | Out-Null
    tar -xzf $tgz -C (Join-Path $repo "src-tauri\pyembed")
}
else { Write-Host "[1/4] 嵌入式 Python 已存在，跳过下载" }

# 2. 依赖装入嵌入式 Python（faos-app 必须非 editable，保证便携）
Write-Host "[2/4] 安装 Python 依赖 ..."
& $pyembed\python.exe -m pip install --upgrade pip 2>&1 | Out-Null
& $pyembed\python.exe -m pip install ./src-tauri vnpy vnpy_ctp vnpy_spreadtrading
if ($LASTEXITCODE -ne 0) { throw "pip install 失败" }

# 2.5 生成发行配置（剥离账号/密码，只保留服务器地址等非账号信息）
Write-Host "[2.5/4] 生成 settings.dist.json（无凭据，临时目录构建后清理）..."
$distDir = Join-Path $repo "src-tauri\dist-settings"
New-Item -ItemType Directory -Force -Path $distDir | Out-Null
$distCfg = Join-Path $distDir "settings.dist.json"
& $pyembed\python.exe -c "import json,pathlib; s=json.loads(pathlib.Path(r'$(Join-Path $repo 'config\settings.local.json')').read_text(encoding='utf-8')); s.pop('mqtt',None); s.setdefault('ctp',{}).update({'用户名':'','密码':''}); pathlib.Path(r'$distCfg').write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8')"
if ($LASTEXITCODE -ne 0) { throw "生成发行配置失败" }

# 2.6 清理打包冗余（NSIS 单线程逐文件解包，减文件数与换快解压是唯二杠杆）：
#     a) PySide6 及 Qt 孤儿（pyqtgraph/qdarkstyle/qtpy/shiboken6）——vnpy 拖入，FAOS 用 Tauri
#     b) FAOS 不用的 vnpy 依赖：plotly(K线图)/PIL/zmq/moocore/narwhals/deap/jsonschema/rpds/
#        matplotlib/patsy/statsmodels/IPython/pip——真实 import 链验证 DEAD（talib/pandas/numpy 保留）
#     c) __pycache__/.pyc——首次启动自动重建
#     实测：47.3s → zlib 33.3s → 剔除 PySide6 22.4s；包体 193MB → 71.5MB（剔库后预计 ~55MB）
# 注意：pip/setuptools/wheel 不删——下次构建的依赖安装还要用（清理在每次 pip install 之后执行）
$deadPkgs = @("PySide6", "pyqtgraph", "qdarkstyle", "qtpy", "shiboken6", "plotly", "PIL",
    "zmq", "pyzmq", "moocore", "narwhals", "deap", "jsonschema", "rpds", "matplotlib", "patsy",
    "statsmodels", "IPython", "nbformat", "jupyter_core", "fastjsonschema", "_plotly_utils",
    "jupyter", "_distutils_hack")
foreach ($pkg in $deadPkgs) {
    Remove-Item -Recurse -Force (Join-Path $pyembed "Lib\site-packages\$pkg") -ErrorAction SilentlyContinue
}
Get-ChildItem $pyembed -Directory | Where-Object { $n = $_.Name; ($deadPkgs | Where-Object { $n -like "$pkg*" }).Count -gt 0 } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem (Join-Path $pyembed "Lib\site-packages") -Directory -Filter "*.dist-info" | Where-Object { $n = $_.Name; $base = ($n -split "-")[0]; ($deadPkgs -contains $base) } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $pyembed -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
Get-ChildItem $pyembed -Recurse -Include "*.pyc", "*.pyo" -File -ErrorAction SilentlyContinue | Remove-Item -Force

# 3. 前端依赖
Write-Host "[3/4] npm install ..."
Push-Location $repo
npm install

# 3.5 版本命名：beta 自增（默认）或正式包（-Release）
$bundleCfg = Join-Path $repo "src-tauri\tauri.bundle.json"
$cfg = Get-Content $bundleCfg -Raw -Encoding UTF8 | ConvertFrom-Json
$base = "0.1.0"
if ($Release) {
    $version = $base
    Write-Host "[3.5/4] 正式包版本：$version"
} else {
    $nsisDir = Join-Path $repo "src-tauri\target\bundle-release\bundle\nsis"
    $max = 0
    if (Test-Path $nsisDir) {
        Get-ChildItem $nsisDir -Filter "FAOS_${base}-beta.*_x64-setup.exe" -ErrorAction SilentlyContinue | ForEach-Object {
            if ($_.Name -match "-beta\.(\d+)_") { $max = [Math]::Max($max, [int]$Matches[1]) }
        }
    }
    $version = "${base}-beta.$($max + 1)"
    Write-Host "[3.5/4] beta 包版本：$version（现有最大 beta.$max）"
}
$cfg.version = $version
$cfg | ConvertTo-Json -Depth 20 | Set-Content $bundleCfg -Encoding UTF8

# 3.6 版本号写入 pyembed 的 faos_app/_version.py（启动日志带版本，2026-10-24）
$verFile = Join-Path $pyembed "Lib/site-packages/faos_app/_version.py"
if (Test-Path $verFile) {
    Set-Content -Path $verFile -Value "__version__ = `"$version`"" -Encoding UTF8
    Write-Host "[3.6/4] 版本号已写入 $version"
} else {
    Write-Host "[3.6/4] 警告：未找到 $verFile（版本号保持 dev）"
}

# 4. tauri build（standalone bundle 配置 + bundle-release profile）
Write-Host "[4/4] tauri build ..."
$env:PYO3_PYTHON = "$pyembed\python.exe"
npm run tauri -- build --config=src-tauri/tauri.bundle.json --bundles nsis -- --profile bundle-release
if ($LASTEXITCODE -ne 0) { throw "tauri build 失败" }

# 5. 构建后清理临时发行配置（NSIS 已嵌入安装包，磁盘不留中间产物；
#    2026-10-20 补：注释一直声称自动清理但缺此步骤，dist-settings 磁盘残留）
Remove-Item -Recurse -Force $distDir -ErrorAction SilentlyContinue
Write-Host "已清理临时目录 $distDir"

Pop-Location
Write-Host "`n完成。安装包位于 src-tauri\target\bundle-release\bundle\nsis\"
