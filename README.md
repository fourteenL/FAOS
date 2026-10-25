# FAOS 期货差价套利交易软件

上期所（SHFE）跨期价差套利桌面工具：**人工填入差价**挂触发单，本地由两腿实时 tick 合成四向价差，到价自动触发 vnpy 价差算法拆腿下单（A 腿主动追价、B 腿被动对冲），支持一键跨期做空 / 做多 / 平仓。

> 基于 **pytauri**（Python 经 PyO3 内嵌 Tauri 2 单进程）+ **Vue 3 / JavaScript / Ant Design Vue / Pinia**。
> 交易环境为 **SimNow 模拟**（经纪商代码 9999），未做实盘风控，请勿直接用于实盘。

## 核心功能

- **行情区（上）**：每行一个策略——编号、A/B 合约、A 撤单数、四向差价（人工填入的保存价）、双向持仓（做空红 / 做多绿 / 锁仓可见）、状态、单份、总份；工具条排序（自定义 / 合约升降序 / 拖动调序弹窗）；持仓格悬停显示**柜台实际**双向今昨持仓，与无限易快速核对。
- **策略管理（下左）**：手动输入合约对或按品种订阅（B 腿固定主力：配置 > 持仓量实测 > 种子 > 日期推断；当月交割与远月死腿自动剔除）；今昨仓管理（A/B 腿四输入框平到目标，平今/平昨、只平不开）；交易参数（A/B 腿滑点与报单类型、A 腿撤单上限）。
- **做空/做多面板（下右）**：填价挂触发单（做空/做多可并存，反方向=正常开仓锁仓）；**平仓唯一入口=对应区「平仓」按钮**（份数=单份、封顶该方向持仓、按策略今昨账本显式平昨/平今、先平昨省费）；平仓等待可视化（已等时长 / 重挂次数 / 执行中）；腿停滞可视化（A 停 Xs · B 停 Ys / 无行情）。
- **持仓记账**：双向记账（多头份/空头份独立累计，净额=差），总份限制**按方向独立**（做多/做空各限总份、互不占用，可锁仓双向各满）；策略级今昨账本（leg_book）驱动平仓拆分，跨交易日自动今转昨（夜盘 21:00 归次日）。
- **风控体系**：收盘/休市时段表风控（`config/market_sessions.json`，各时段 end 前可配秒数自动 stop_all + 锁新单，时点后兜底撤柜台全部剩余挂单）；A 腿撤单上限 2000（触限急停+撤两腿挂单）；追价 300s 看门狗（零成交超时=停+原价重挂触发单，重挂带次数标记）；开仓前对向昨仓拦截（拒单点名，防平掉共享腿他策略昨仓）。
- **稳定性**：登录停滞温和重连（严禁销毁网关）；单实例锁；关闭应用自动停止所有活动（撤全部挂单+正常登出）；同账号异地登录自动清理上一柜台会话残留挂单。

### 差价触发语义

| 动作 | 触发条件 | 价差公式 | 执行方向 |
|---|---|---|---|
| 做空（开） | 空开 ≥ X | a.bid − b.ask | 卖A买B（SHORT，锁仓开仓） |
| 做多（开） | 多开 ≤ X | a.ask − b.bid | 买A卖B（LONG，锁仓开仓） |
| 平空（按钮） | 多开 ≤ X | — | 买回价差（净仓，消耗空头份） |
| 平多（按钮） | 空开 ≥ X | — | 卖出价差（净仓，消耗多头份） |

立即触发口诀：做空填价 ≤ 当前空开；做多填价 ≥ 当前多开。总份校验：`该方向持仓 + 在途开仓 + 本单 ≤ 总份`（方向独立，平仓不占额度）。

## 架构

```
┌ Tauri 2 (Rust 壳) ─────────────────────────────────┐
│  WebView: Vue 3 + AntD Vue + Pinia                 │
│    ▲ Emitter 事件(py://*)    │ pyInvoke 指令        │
├── PyO3 内嵌 Python（faos_app）──────────────────────┤
│  main(装配) commands push    │ strategy_manager     │
│  local_spread(两腿tick→四向价差) trigger_monitor    │
│  spread_service + spread_algo(FaosTaker) leg_ops   │
│  leg_book(今昨账本) ctp(vnpy_ctp) contract_seed    │
└── CTP/SimNow 行情+交易 ────────────────────────────┘
```

- **行情链路本地化**：订阅品种 → 本地展开组合 → CTP 两腿实时 tick → 本地合成四向价差 → 触发监控（差价全部人工填入，无外部行情服务）。
- vnpy/CTP 线程回调经 anyio BlockingPortal 串行化后处理并推送前端；退出=优雅断开+`os._exit`。
- 事件推送 `py://`：strategy / notice / spread（实时价差+停滞秒数）/ order / trade / algo / conn-state。

## 环境要求

- Windows 10/11 x64（vnpy_ctp 仅支持 Windows）
- Python 3.13（仓库根 `.venv`）；Node.js ≥ 20 与 npm；Rust toolchain（msvc）
- 依赖锁定：`pytauri==0.8.*`、`pydantic 2`、`anyio 4`、`vnpy 4.4`、`vnpy_ctp 6.7.11.*`、`vnpy_spreadtrading 1.4.0`；Rust 侧 `tauri==2.8.3`（勿随意升级）

## 开发

```bash
# Python 依赖（可编辑安装，供 tauri dev 内嵌运行与 pytest 导入）
.venv/Scripts/python -m pip install -e src-tauri pytest
npm install

# 开发运行（需 cargo 在 PATH：export PATH="$HOME/.cargo/bin:$PATH"）
npm run tauri dev

# 测试（后端 236 + 前端 87）
.venv/Scripts/python -m pytest src-tauri/tests -q
npx vitest run
```

## 配置（config/ 目录）

| 文件 | 说明 |
|---|---|
| `settings.local.json` | 本地凭据与参数（git 忽略；结构见 `settings.example.json`；三层合并：内置默认 → 本地 → %APPDATA% 运行时覆盖） |
| `market_sessions.json` | 交易时段表（日盘三节+夜盘有色 01:00/贵金属 02:30，可手工编辑，驱动收盘风控；设置抽屉可改） |
| `ctp_environments.json` | 柜台环境表（SimNow 四前置，登录页可增删；文件 > 内置默认兜底） |
| `contract_seed.json` | 合约种子（CTP 就绪自动刷新，离线兜底） |

运行时数据在 `%APPDATA%\faos\`：`settings.json`（界面保存的覆盖层）、`strategies.json`（策略/触发单/账本，原子写）、`logs/faos-YYYY-MM-DD.log`。

## 打包分发

```powershell
# 安装包（NSIS，beta 版本号自动递增；config 文件随包分发）
powershell -ExecutionPolicy Bypass -File scripts/build_release.ps1
# 便携版
powershell -ExecutionPolicy Bypass -File scripts/build_portable.ps1
```

脚本自动完成：下载 python-build-standalone 3.13.15 到 `src-tauri/pyembed/` → 安装依赖（faos-app 非 editable）→ 剔除冗余库 → 版本号写入 → `tauri build`。产物：`src-tauri/target/bundle-release/bundle/nsis/FAOS_0.1.0-beta.N_x64-setup.exe`（约 55MB）。安装 perMachine（C:\Program Files\FAOS），旧版先卸载再安装。

## 诊断

日志在 `%APPDATA%\faos\logs\`（保留期可配），启动首行带版本号。关键标记：`FAOS_TRIGGER_PLACED/FIRED/REJECT`（带持仓上下文）、`FAOS_ALGO_DONE`、`FAOS_LEG_BOOK`（成交入账）、`FAOS_CLOSE_RISK / FAOS_CLOSE_SWEEP`（收盘风控两阶段）、`FAOS_WATCHDOG_TIMEOUT`（追价超时+重挂次数）、`FAOS_CANCEL_BENIGN`（撤单竞态）、`FAOS_LEDGER_CLAMPED`（启动账本校正）、`FAOS_STALE_ORDERS_CLEANED`（会话清理）、`FAOS_OP`（操作审计）。

> `docs/`、`api.md`、`progress.md` 与各测试目录为本地文件，不入库（见 .gitignore）；测试在本机运行。

## 已知限制

- 本仓库不含任何凭据；未配置 `config/settings.local.json` 时应用可启动但无法连接交易
- 仅支持上期所（SHFE 双向持仓是策略隔离设计的前提）；本地时钟无节假日（节假日无行情价差不触发，风控风险极低）
- SimNow 每组前置仅一处会话：双开 FAOS / 双机同账号会互踢
- 重启不恢复运行中的算法（清除状态并告警，人工核对持仓）
- 密码明文落盘本地文件（单机工具的设计取舍，注意文件权限）

## 免责声明

本软件面向 SimNow 模拟环境的学习与策略演练。未包含实盘级风控（资金校验、流控、异常行情熔断等），接入实盘前需自行评估并补充。
