# FAOS 免安装（绿色）版组装脚本
# 产物：Desktop\FAOS-portable\（faos.exe + 嵌入式 Python + settings.local.json）
# 用法：先跑 build_release.ps1 的 1/2 步（或手动 pip install 到 pyembed），再：
#   powershell -ExecutionPolicy Bypass -File scripts/build_portable.ps1 [-SkipBuild]
# 注意：target/bundle-release/ 里 bundle 时拷的 python 资源是旧 site-packages，
# 必须用 src-tauri/pyembed/python（pip 刚装过最新 faos_app 的源目录）组装。

param([switch]$SkipBuild)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$pyembed = Join-Path $repo "src-tauri\pyembed\python"
$out = Join-Path ([Environment]::GetFolderPath("Desktop")) "FAOS-portable"

if (-not $SkipBuild) {
    # 依赖已在 pyembed（build_release.ps1 第 2 步），这里只做 tauri build（免 NSIS 打包）
    $env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
    $env:PYO3_PYTHON = "$pyembed\python.exe"
    Push-Location $repo
    npm run tauri -- build --config=src-tauri/tauri.bundle.json --no-bundle -- --profile bundle-release
    if ($LASTEXITCODE -ne 0) { throw "tauri build 失败" }
    Pop-Location
}

if (-not (Test-Path "$pyembed\python.exe")) { throw "嵌入式 Python 不存在，先跑 build_release.ps1" }
New-Item -ItemType Directory -Force -Path $out | Out-Null
Copy-Item (Join-Path $repo "src-tauri\target\bundle-release\faos.exe") $out -Force
Copy-Item (Join-Path $repo "config\settings.local.json") $out -Force
robocopy $pyembed $out /E /NFL /NDL /NJH /NJS /NP
Write-Host "`n完成：$out（faos.exe + python + settings.local.json）"
