# FAOS 期货差价套利交易软件

上期所跨期价差套利桌面工具：实时监控行情服务推送的四向价差，按差价条件自动触发 vnpy 价差算法拆腿下单（A 腿主动追价、B 腿被动对冲、自动补腿），并支持两腿细粒度状态查看与单腿撤单。

> 基于 **pytauri**（Python 经 PyO3 内嵌 Tauri 2 单进程）+ **Vue 3 / JavaScript / Ant Design Vue**。
> 交易环境为 **SimNow 模拟**（经纪公司代码 9999），未做实盘风控，请勿直接用于实盘。

## 功能

- **行情区（上）**：每行一个策略，15 列——编号、A/B 合约、A撤单、空开/空平、A/B 空仓、多开/多平、A/B 多仓、状态、单份、总份；价差变动涨红跌绿。顶栏**手动连接**：MQTT（7×24 开放）与 CTP（SimNow 仅交易时段可登录）均由用户点击「连接」发起，可随时断开重连。
- **策略管理（下左）**：手动输入合约对或按品种订阅（服务端自动展开全部跨期组合，各生成一行）；订阅 / 停止 / 删除；本地持久化，重启恢复。
- **做空/做多面板（下右）**：填入差价挂触发单（同一策略最多 1 张开仓 + 1 张空平 + 1 张多平），行情满足条件自动执行；腿部卡片实时显示两腿委托/成交/持仓；单份/总份编辑与限额控制。
- **交易执行**：vnpy_spreadtrading `SpreadTaker` 算法——B 腿（主力）按盘口被动挂单，A 腿按差价反推限价主动追价，单腿先成交自动补齐另一腿；追价超时看门狗自动停止并告警。
- **平仓**：手动挂空平/多平触发单，系统按净持仓方向反向对冲；held_units 按实际成交量累计，CTP 登录后以真实持仓校正。

### 差价触发语义

| 动作 | 触发条件 | 执行方向 |
|---|---|---|
| 做空（开） | 空开 ≥ X（a.bid − b.ask） | 卖A买B（SHORT） |
| 做多（开） | 多开 ≤ X（a.ask − b.bid） | 买A卖B（LONG） |
| 空平 | 空平 ≤ X | 买回价差（LONG） |
| 多平 | 空开 ≥ X | 卖出价差（SHORT） |

限额：`|净持仓份数| + 本次手数 ≤ 总份`；触发单一次性，触发即移除。

## 架构

```
┌ Tauri 2 (Rust 壳) ──────────────────────────────┐
│  WebView: Vue 3 + AntD Vue + Pinia              │
│    ▲ Emitter 事件(py://*)   │ pyInvoke 指令      │
├── PyO3 内嵌 Python（faos_app）───────────────────┤
│  main(装配) commands push ｜ strategy_manager    │
│  trigger_monitor mqtt_feed(paho) ctp(vnpy)      │
│  spread_service(vnpy_spreadtrading) leg_ops     │
└─── MQTT 行情 ──────────── CTP/SimNow 交易 ───────┘
```

线程模型：主线程跑 Tauri 事件循环；paho 网络线程与 vnpy EventEngine 线程的回调经 anyio BlockingPortal 串行化后处理并推送前端。

## 环境要求

- Windows 10/11 x64（vnpy_ctp 仅支持 Windows）
- Python 3.13（仓库根 `.venv`）；Node.js ≥ 20 与 npm；Rust toolchain（msvc，rustup）
- 依赖版本锁定：`pytauri==0.8.*`、`pydantic 2`、`anyio 4`、`paho-mqtt 2.*`、`vnpy 4.4`、`vnpy_ctp 6.7.11.*`、`vnpy_spreadtrading 1.4.0`；Rust 侧 `tauri==2.8.3`（pytauri 0.8 兼容上限，勿随意升级）

## 开发

```bash
# Python 依赖（可编辑安装，供 tauri dev 内嵌运行与 pytest 导入）
.venv/Scripts/python -m pip install -e src-tauri pytest
npm install

# 开发运行（需 cargo 在 PATH：export PATH="$HOME/.cargo/bin:$PATH"）
npm run tauri dev
```

测试（105 个）：

```bash
.venv/Scripts/python -m pytest src-tauri/tests -q   # 后端 89
npx vitest run                                      # 前端 16
```

> 注意：`faos_app` 的 `commands.py`/`main.py`/`push.py` 依赖 pytauri 运行时注入的 ext_mod，
> 只能在 Tauri 进程内运行；单元测试覆盖其余纯逻辑服务层，命令层以 dev 运行冒烟验证。

## 打包发布

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_release.ps1
```

脚本自动完成：下载 python-build-standalone 3.13.15 到 `src-tauri/pyembed/` → 安装全部依赖（faos-app 非 editable）→ `tauri build --config src-tauri/tauri.bundle.json --profile bundle-release`。产物：

- `src-tauri/target/bundle-release/bundle/nsis/FAOS_0.1.0_x64-setup.exe`（安装版，绿色分发用同目录 `faos.exe` + `python/` 资源）

## 配置与数据

全部位于 `%APPDATA%\faos\`：

| 文件 | 说明 |
|---|---|
| `settings.json` | MQTT broker/账号、CTP 连接（中文键，与 vnpy_ctp 网关一致）、算法参数（payup/interval/timeout）。界面「设置」抽屉可改，密码回显掩码，重启后生效 |
| `strategies.json` | 策略、触发单、单份/总份、held_units（原子写） |
| `faos.log` | 诊断日志（启动/行情/回执/异常），排查问题先看这里 |

默认 SimNow 前置为交易时段地址 `180.168.146.187:10130/10131`（柜台环境=测试），7x24 地址可在设置中切换。

> `docs/`、`api.md`（MQTT 行情对接协议）与各测试目录为本地文件，不入库（见 .gitignore）；测试在本机运行（110 后端 + 45 前端）。

## 已知限制

- 本仓库不含任何凭据；未配置 `config/settings.local.json` 时应用可启动但无法连接行情/交易
- 本仓库开发环境对 MQTT broker 与 SimNow 前置网络不可达，行情/交易链路需在可达网络按验收清单实测
- MQTT client_id 固定，双开实例会互相挤掉线
- 重启不恢复运行中的算法（vnpy 算法为运行态），会清除其状态并告警提示人工核对持仓
- 密码明文落盘本地文件（单机工具的设计取舍，请注意文件权限）

## 免责声明

本软件面向 SimNow 模拟环境的学习与策略演练。未包含实盘级风控（资金校验、流控、异常行情熔断等），接入实盘前需自行评估并补充。
