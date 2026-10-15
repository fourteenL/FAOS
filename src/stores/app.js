import { defineStore } from "pinia";
import { call, listenPy } from "../api/pytauri.js";

export const STATUS_ZH = {
  unsubscribed: "未订阅",
  pending: "等待行情",
  active: "空闲",
  armed: "监控中",
  executing: "执行中",
  stopped: "已停止",
};

/** 策略所属品种：显式 product 优先，否则取 A 腿前导字母（ag2702 -> ag）。 */
export function productOf(s) {
  if (s.product) return s.product;
  const m = String(s.leg_a || "").match(/^[a-zA-Z]+/);
  return m ? m[0].toLowerCase() : "";
}

/** 最小变动价位的小数位数（au 0.02 -> 2；ag 1 -> 0）。 */
export function tickPrecision(tick) {
  const s = String(tick);
  return s.includes(".") ? s.split(".")[1].length : 0;
}

export const useAppStore = defineStore("app", {
  state: () => ({
    strategies: [],
    positions: {},   // vt_symbol -> { long: n, short: n }
    orders: [],      // 腿部委托回报（T12 用）
    trades: [],      // 腿部成交回报（T12 用）
    algos: [],       // 价差算法状态（T12 用）
    notices: [],
    spreadQuotes: {},  // pair -> { short_open, long_open, live }（本地合成实时价差，填价参考）
    accountPositions: {},  // vt_symbol -> { long_today,long_yd,short_today,short_yd }（柜台真实持仓，含双向）
    ctpState: "disconnected",
    ctpStep: "",  // 连接进度步骤（后端关键日志映射）
    selectedId: null,
    products: [],
    productFilter: null,
    productTicks: {},
    cancelledSet: {},  // vt_orderid 去重
    cancelByPair: {},  // pair -> { vt_symbol: 已撤笔数 }
    loggedIn: false,
    bound: false,
  }),
  getters: {
    selected(state) {
      return state.strategies.find((s) => s.id === state.selectedId) || null;
    },
    tickOf(state) {
      return (s) => {
        const p = productOf(s);
        const t = state.productTicks[p];
        return t != null ? t : 1;
      };
    },
    filteredStrategies(state) {
      if (!state.productFilter) return state.strategies;
      return state.strategies.filter((s) => productOf(s) === state.productFilter);
    },
    /** 行情表展示集：品种筛选 ∩ 已订阅（未订阅行不展示，管理在订阅抽屉） */
    visibleStrategies(state) {
      // 自定义排序（2026-10-14 用户定稿）：按 sort_key（创建追加递增，表格 ↑↓ 可调）
      return this.filteredStrategies
        .filter((s) => s.status !== "unsubscribed")
        .sort((a, b) => (a.sort_key ?? 0) - (b.sort_key ?? 0) || a.id - b.id);
    },
  },
  actions: {
    async init() {
      // 先绑定事件再拉状态：连接成功事件不会因「后端先连上、前端后绑定」而丢失
      if (!this.bound) this.bindEvents();
      const status = await call("get_app_status");
      this.ctpState = status.ctp;
      this.products = status.products || [];
      this.productTicks = status.product_ticks || {};
      this.strategies = await call("list_strategies");
    },
    bindEvents() {
      this.bound = true;
      listenPy("strategy", (p) => this.upsertStrategy(p.strategy));
      listenPy("strategy-deleted", (p) => {
        this.strategies = this.strategies.filter((x) => x.id !== p.id);
        if (this.selectedId === p.id) this.selectedId = null;
      });
      listenPy("notice", (p) => {
        this.notices.push({ ...p, ts: Date.now() });
        if (this.notices.length > 50) this.notices.shift();
      });
      listenPy("spread", (p) => {  // 本地合成实时价差（0.5s 节流推送；停滞推 live:false）
        this.spreadQuotes[p.pair] = p;
      });
      listenPy("conn-state", (p) => {
        if (p.kind === "ctp") {
          this.ctpState = p.state;
          this.ctpStep = p.msg || "";  // 进度步骤（就绪/断开时清空由后端控制）
          if (p.state === "disconnected") this.ctpStep = "";
        }
      });
      listenPy("position", (p) => {
        const sym = p.vt_symbol;
        const cur = this.positions[sym] || { long: 0, short: 0 };
        this.positions[sym] = {
          long: p.direction === "多" ? p.volume : p.direction === "long" ? p.volume : cur.long,
          short: p.direction === "空" ? p.volume : p.direction === "short" ? p.volume : cur.short,
        };
      });
      listenPy("order", (p) => {
        const i = this.orders.findIndex((o) => o.vt_orderid === p.vt_orderid);
        if (i >= 0) this.orders.splice(i, 1, p);
        else this.orders.unshift(p);
        // A/B 腿已撤销委托累计计数（同一委托多次回报按 vt_orderid 去重）
        if (p.status_zh === "已撤销" || p.status === "已撤销" || p.status === "CANCELLED") {
          if (!this.cancelledSet[p.vt_orderid]) {
            this.cancelledSet[p.vt_orderid] = true;
            const pair = p.pair || this.pairOfSymbol(p.vt_symbol);
            if (pair) {
              const bySym = (this.cancelByPair[pair] = this.cancelByPair[pair] || {});
              bySym[p.vt_symbol] = (bySym[p.vt_symbol] || 0) + 1;
            }
          }
        }
      });
      listenPy("trade", (p) => {
        this.trades.unshift(p);
        if (this.trades.length > 200) this.trades.pop();
      });
      listenPy("algo", (p) => {
        const i = this.algos.findIndex((a) => a.algoid === p.algoid);
        if (i >= 0) this.algos.splice(i, 1, p);
        else this.algos.unshift(p);
      });
    },
    upsertStrategy(s) {
      const i = this.strategies.findIndex((x) => x.id === s.id);
      if (i >= 0) this.strategies.splice(i, 1, s);
      else this.strategies.push(s);
    },
    select(id) {
      this.selectedId = id;
    },
    pairOfSymbol(vtSymbol) {
      const leg = String(vtSymbol || "").split(".")[0];
      const s = this.strategies.find((x) => x.leg_a === leg || x.leg_b === leg);
      return s ? s.pair : null;
    },
    cancelCount(pair, vtSymbol) {
      return (this.cancelByPair[pair] || {})[vtSymbol] || 0;
    },
    // -- 命令包装 ---------------------------------------------------------
    async createStrategy(body) {
      this.strategies = await call("create_strategy", body);
      this.productFilter = null; // 重置筛选，确保新订阅行可见
    },
    async subscribe(id) {
      const s = await call("subscribe_strategy", { strategy_id: id });
      this.upsertStrategy(s);
    },
    async removeStrategy(id) {
      this.strategies = await call("delete_strategy", { strategy_id: id });
    },
    async removeProduct(product) {
      this.strategies = await call("delete_product", { product });
    },
    async stopAll() {
      await call("stop_all", {});
      for (const s of this.strategies) {
        s.triggers = [];
        s.active_algos = [];
        s.status = "active";
      }
    },
    async stopStrategy(id) {
      const s = await call("stop_strategy", { strategy_id: id });
      this.upsertStrategy(s);
    },
    async setLots(id, body) {
      const s = await call("set_lots", { strategy_id: id, ...body });
      this.upsertStrategy(s);
    },
    async setConfig(id, body) {
      const s = await call("set_strategy_config", { strategy_id: id, ...body });
      this.upsertStrategy(s);
    },
    async stopDirection(id, side) {
      const s = await call("stop_strategy_direction", { strategy_id: id, side });
      this.upsertStrategy(s);
    },
    async placeTrigger(id, body) {
      return call("place_trigger", { strategy_id: id, ...body });
    },
    async cancelTrigger(id, triggerId) {
      const s = await call("cancel_trigger", { strategy_id: id, trigger_id: triggerId });
      this.upsertStrategy(s);
    },
    async refreshLegOrders(id) {
      return call("get_leg_orders", { strategy_id: id });
    },
    async refreshAccountPositions() {
      try {
        this.accountPositions = await call("list_account_positions");
      } catch (e) {
        this.accountPositions = {};  /* CTP 未连接 */
      }
    },
    async closeLegTarget(id, leg, targets) {
      return call("close_leg_target", { strategy_id: id, leg, ...targets });
    },
    async reorderStrategies(orderedIds) {
      const list = await call("reorder_strategies", { ordered_ids: orderedIds });
      for (const s of list || []) this.upsertStrategy(s);
    },
    async cancelLegOrders(id, leg) {
      return call("cancel_leg_orders", { strategy_id: id, leg });
    },
    async login(body) {
      await call("login", body);
      this.loggedIn = true;
    },
    async getLoginState() {
      return call("get_login_state", {});
    },
    async connectCtp() {
      await call("connect_ctp", {});
      this.ctpState = "logining";
    },
  },
});
