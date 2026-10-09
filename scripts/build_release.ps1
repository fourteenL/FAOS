# FAOS standalone 发布构建脚本
# 产物：src-tauri/target/bundle-release/bundle/nsis/FAOS_x.y.z_x64-setup.exe（及 msi）
# 前置：rustup(msvc)、node/npm 已安装；本脚本自动准备嵌入式 Python。

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
& $pyembed\python.exe -m pip install ./src-tauri "paho-mqtt==2.*" vnpy vnpy_ctp vnpy_spreadtrading
if ($LASTEXITCODE -ne 0) { throw "pip install 失败" }

# 2.5 生成发行配置（剥离账号/密码，只保留服务器地址等非账号信息）
Write-Host "[2.5/4] 生成 settings.dist.json（无凭据）..."
$distCfg = Join-Path $repo "config\settings.dist.json"
& $pyembed\python.exe -c "import json,pathlib; s=json.loads(pathlib.Path(r'$(Join-Path $repo 'config\settings.local.json')').read_text(encoding='utf-8')); s.setdefault('mqtt',{}).update(user='',password=''); s.setdefault('ctp',{}).update({'用户名':'','密码':''}); pathlib.Path(r'$distCfg').write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8')"
if ($LASTEXITCODE -ne 0) { throw "生成发行配置失败" }

# 2.6 清理 .pyc/__pycache__：NSIS 单线程逐文件解包，~5600 个缓存文件占 29% 文件数，
#     删除后安装 47s→40s、包体 193MB→177MB；首次启动自动重建缓存（实测启动正常）
Get-ChildItem $pyembed -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
Get-ChildItem $pyembed -Recurse -Include "*.pyc", "*.pyo" -File -ErrorAction SilentlyContinue | Remove-Item -Force

# 3. 前端依赖
Write-Host "[3/4] npm install ..."
Push-Location $repo
npm install

# 4. tauri build（standalone bundle 配置 + bundle-release profile）
Write-Host "[4/4] tauri build ..."
$env:PYO3_PYTHON = "$pyembed\python.exe"
npm run tauri -- build --config=src-tauri/tauri.bundle.json -- --profile bundle-release
if ($LASTEXITCODE -ne 0) { throw "tauri build 失败" }

Pop-Location
Write-Host "`n完成。安装包位于 src-tauri\target\bundle-release\bundle\nsis\"
