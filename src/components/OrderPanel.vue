<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import { message } from "ant-design-vue";
import { useAppStore, tickPrecision } from "../stores/app.js";
import LegCard from "./LegCard.vue";

const store = useAppStore();
const form = reactive({ open_short: null, close_short: null, open_long: null, close_long: null });
const legs = ref({ leg_a: null, leg_b: null });

const s = computed(() => store.selected);

// py://algo 事件后刷新腿部快照（委托/成交变化）
function bindAlgoRefresh() {
  // store.algos 由全局事件更新；监听其变化刷新腿部
  watch(
    () => [store.algos.length, store.orders.length, store.trades.length],
    () => refreshLegs()
  );
}
onMounted(() => {
  if (s.value) refreshLegs();
  bindAlgoRefresh();
});

async function refreshLegs() {
  if (!s.value) return;
  try {
    legs.value = await store.refreshLegOrders(s.value.id);
  } catch (e) {
    /* CTP 未连接时保持空 */
  }
}

// 品种最小变动价位 → 输入框步进/精度
const tick = computed(() => (s.value ? store.tickOf(s.value) : 1));
const step = computed(() => tick.value);
const precision = computed(() => tickPrecision(tick.value));

// 实时合成价差（填价参考，0.5s 节流推送）：空开=做空触发参考、多开=做多触发参考
const cur = computed(() => store.spreadQuotes[s.value?.pair]);
const fmtLive = (v) => (cur.value?.live && v != null ? Number(v).toFixed(precision.value) : "--");

// 腿停滞可视化（2026-10-19 定稿）：停滞时显示各腿停 tick 秒数（远月/收盘可见）；
// stale=null=从未收到行情。任一腿超阈值即不合成不触发（阈值=设置「行情停滞阈值」）
const staleText = computed(() => {
  const c = cur.value;
  if (!c || c.live) return "";
  const fmtS = (v) => (v == null ? "无行情" : `停 ${v}s`);
  return `A ${fmtS(c.stale_a)} · B ${fmtS(c.stale_b)}`;
});


// 差价来源=人工：面板预填策略保存的价；下单时后端自动保存新价回策略。
// 脏字段保护：用户改过的输入不被策略事件重置——追价期每次 A 腿撤单都推策略更新
// （撤单计数变化），旧实现会把你正在输入的价格打回旧保存值（表现为"下单后价格变/负价变正"）
const dirty = new Set();
const markDirty = (field) => dirty.add(field);
let lastId = null;

watch(
  () => [s.value?.id, s.value?.triggers.length, s.value?.manual_open_short, s.value?.manual_open_long,
         s.value?.manual_close_short, s.value?.manual_close_long],
  () => {
    if (!s.value) {
      return;
    }
    if (dirty.size && s.value?.id !== lastId) {
      dirty.clear();  // 切换策略：恢复预填
    }
    lastId = s.value?.id;
    const trigOf = (a) => (s.value.triggers || []).find((t) => t.action === a);
    for (const [field, manual, action] of [
      ["open_short", "manual_open_short", "open_short"],
      ["close_short", "manual_close_short", "close_short"],
      ["open_long", "manual_open_long", "open_long"],
      ["close_long", "manual_close_long", "close_long"],
    ]) {
      if (dirty.has(field)) {
        continue;  // 用户输入保住
      }
      const t = trigOf(action);
      form[field] = t ? t.spread : s.value[manual];  // 挂单字段显示挂单价，否则显示保存价
    }
    refreshLegs();
  },
  { immediate: true }
);

// 无触发单且无运行算法 → 无可停止的活动，停止按钮置灰
// 停止按方向（做空/做多并存后互不影响）：本方向无挂单且无运行算法 → 置灰
const sideOfAction = (action) => (action === "open_short" || action === "close_short" ? "short" : "long");
const stopDisabled = (side) => {
      const st = s.value;
      if (!st) return true;
      const sideLive = (st.triggers || []).some((t) => sideOfAction(t.action) === side);
      return !sideLive && st.active_algos.length === 0;
};
const openTriggerOf = (action) => s.value?.triggers.find((t) => t.action === action);

const OPEN_INPUT = { open_short: "open_short", open_long: "open_long" };

// 平仓唯一入口=反向区「平仓」按钮（is_close 通道）；反方向挂单=正常开仓锁仓
// （2026-10-19 夜盘实测后终稿：上期所双向持仓=设计效果，反向置灰一律移除）
async function doPlace(action, spread, replaceTriggerId) {
  const companion = action === "open_short" ? form.close_short
    : action === "open_long" ? form.close_long : null;  // 开仓时对应平仓价一并保存
  try {
    await store.placeTrigger(s.value.id, {
      action, spread, lots: s.value.single_lots,
      companion_spread: companion ?? undefined,
      replace_trigger_id: replaceTriggerId ?? undefined,
    });
    dirty.delete(action);  // 下单确认：该字段恢复跟随（显示挂单价）
    // 触发条件随单教学（2026-10-15 夜盘实证：空开填 1000 永不触发——方向搞反）
    const isShort = action === "open_short";
    const curTxt = cur.value?.live
      ? `，当前${isShort ? "空开" : "多开"} ${fmtLive(isShort ? cur.value.short_open : cur.value.long_open)}`
      : "";
    message.success(`触发单已挂出：${isShort ? "做空" : "做多"} ${s.value.single_lots} 份，${isShort ? "空开 ≥" : "多开 ≤"} ${spread} 才触发${curTxt}`
      + `${isShort ? "（填价 ≤ 当前空开即立即触发）" : "（填价 ≥ 当前多开即立即触发）"}`);
  } catch (e) {
    message.error(String(e));
  }
}

// 启动：每方向一张开仓挂单（做空/做多可并存，含持反向仓时的反向开仓=锁仓）；
// 同方向已有未触发挂单提示改用「修改」
async function start(action) {
  const spread = form[OPEN_INPUT[action]];
  if (spread == null) {
    message.warning("请填写触发差价");
    return;
  }
  if (openTriggerOf(action)) {
    const which = action === "open_short" ? "做空" : "做多";
    message.warning(`已有未触发的${which}开仓挂单，请用「修改」改价或先「停止」`);
    return;
  }
  await doPlace(action, spread);
}

// 修改：改价 = 先按新价挂替换单（replace 豁免净额校验——持满仓也能改自己挂单的价），
// 成功后再撤旧单；无挂单时等同启动。2026-10-15 夜盘实证：旧流程先撤后挂，持满份时被总份误拒。
async function modify(action) {
  const spread = form[OPEN_INPUT[action]];
  if (spread == null) {
    message.warning("请填写触发差价");
    return;
  }
  const old = openTriggerOf(action);
  try {
    await doPlace(action, spread, old?.trigger_id);
    if (old) {
      await store.cancelTrigger(s.value.id, old.trigger_id);
      message.success("已按新价格重新挂单");
    }
  } catch (e) {
    message.error(String(e));
  }
}

// 停止平仓可点条件：本方向有挂单或算法在跑（含平仓单/开仓单活动——方向级停止一视同仁）
function hasCloseActivity(side) {
  const st = s.value;
  if (!st) return false;
  const sideActs = side === "short"
    ? ["open_short", "close_short", "open_long"] : ["open_long", "close_long", "open_short"];
  // short 区的平仓单是 open_long（反向）——两向活动都算
  const armed = (st.triggers || []).some((t) => t.action.startsWith("open_") || t.action.startsWith("close_"));
  return armed || st.active_algos.length > 0;
}

// 平仓=反向开仓（手动路径保留：持多仓去做空区启动、持空仓去做多区启动）。
// 独立平仓按钮（2026-10-16 用户定稿修订）：按平仓价输入框（空平/多平）挂反向触发单，
// 份数=策略管理的单份（每次触发手数，多次点击逐份平）；不自动取价；同向旧挂单自动替换。
async function closeNow(side) {
  const st = s.value;
  // 按方向双向份（2026-10-20 夜盘修订：锁仓时净额=0，按净额会误判无仓）
  const pool = side === "short" ? (st?.held_short_units || 0) : (st?.held_long_units || 0);
  if (!(pool > 0)) {
    message.warning("无对应持仓可平");
    return;
  }
  const spread = side === "short" ? form.close_short : form.close_long;
  if (spread == null) {
    message.warning(side === "short"
      ? "请填写空平差价（当前多开价附近可尽快触发）"
      : "请填写多平差价（当前空开价附近可尽快触发）");
    return;
  }
  const action = side === "short" ? "open_long" : "open_short";  // 平空=买回=多开方向
  const old = openTriggerOf(action);  // 旧的平仓尝试挂单自动替换
  try {
    await store.placeTrigger(st.id, {
      action, spread, lots: st.single_lots,
      replace_trigger_id: old?.trigger_id,
      is_close_order: true,  // 平仓按钮通道（反向开仓的唯一入口）
    });
    if (old) await store.cancelTrigger(st.id, old.trigger_id);
    message.success(`平仓单已挂出：${side === "short" ? "买回" : "卖出"} ${st.single_lots} 份 @差价 ${spread}`
      + "，到价拆腿（请等完成通知，勿点停止）");
  } catch (e) {
    message.error(String(e));
  }
}

// 停止按方向：撤本方向触发单 + 停本方向算法（挂单/执行互不影响另一方向），持仓保留
async function stopCurrent(side) {
  try {
    await store.stopDirection(s.value.id, side);
    const which = side === "short" ? "做空" : "做多";
    message.success(`已停止${which}侧（撤挂单 + 停算法），持仓保留`);
  } catch (e) {
    message.error(String(e));
  }
}

async function cancelTrigger(triggerId) {
  try {
    await store.cancelTrigger(s.value.id, triggerId);
  } catch (e) {
    message.error(String(e));
  }
}

function fmt(v) {
  return v == null ? "--" : Number(v).toFixed(2);
}

const ACTION_ZH = { open_short: "做空开", open_long: "做多开", close_short: "空平", close_long: "多平" };
</script>

<template>
  <div v-if="s" class="panel">
    <div class="zones">
      <div class="zone">
        <div class="zone-title short">做空（卖A买B）</div>
        <div class="line">
          <label>空开差价</label>
          <a-input-number v-model:value="form.open_short" :step="step" :precision="precision" size="small" style="width: 90px" @change="markDirty('open_short')" />
          <a-button size="small" type="primary" danger @click="start('open_short')">启动</a-button>
          <a-button size="small" @click="modify('open_short')">修改</a-button>
          <a-button size="small" :disabled="stopDisabled('short')" @click="stopCurrent('short')">停止</a-button>
        </div>
        <div class="line">
          <label>空平差价</label>
          <a-input-number v-model:value="form.close_short" :step="step" :precision="precision" size="small" style="width: 90px" @change="markDirty('close_short')" />
          <span class="cur" :class="{ off: !cur?.live }" title="实时空开价差（做空触发条件：空开 ≥ 填价；填价 ≤ 当前值即立即触发）">{{ fmtLive(cur?.short_open) }}</span>
          <span v-if="staleText" class="stale" title="任一腿行情停滞超阈值即不合成不触发（阈值可在设置中调整）；「无行情」=该腿从未收到 tick（远月/夜盘常无行情）">{{ staleText }}</span>
          <a-button size="small" danger :disabled="!(s.held_short_units > 0)" title="按空平差价挂买回单，份数=单份（封顶空头持仓）；多次点击逐份平" @click="closeNow('short')">平仓</a-button>
          <a-button size="small" :disabled="!hasCloseActivity('short')" title="撤销本方向平仓挂单并停止执行中的平仓算法" @click="stopCurrent('short')">停止平仓</a-button>
        </div>
      </div>
      <div class="zone">
        <div class="zone-title long">做多（买A卖B）</div>
        <div class="line">
          <label>多开差价</label>
          <a-input-number v-model:value="form.open_long" :step="step" :precision="precision" size="small" style="width: 90px" @change="markDirty('open_long')" />
          <a-button size="small" type="primary" @click="start('open_long')">启动</a-button>
          <a-button size="small" @click="modify('open_long')">修改</a-button>
          <a-button size="small" :disabled="stopDisabled('long')" @click="stopCurrent('long')">停止</a-button>
        </div>
        <div class="line">
          <label>多平差价</label>
          <a-input-number v-model:value="form.close_long" :step="step" :precision="precision" size="small" style="width: 90px" @change="markDirty('close_long')" />
          <span class="cur" :class="{ off: !cur?.live }" title="实时多开价差（做多触发条件：多开 ≤ 填价；填价 ≥ 当前值即立即触发）">{{ fmtLive(cur?.long_open) }}</span>
          <span v-if="staleText" class="stale" title="任一腿行情停滞超阈值即不合成不触发（阈值可在设置中调整）；「无行情」=该腿从未收到 tick（远月/夜盘常无行情）">{{ staleText }}</span>
          <a-button size="small" danger :disabled="!(s.held_long_units > 0)" title="按多平差价挂卖出单，份数=单份（封顶多头持仓）；多次点击逐份平" @click="closeNow('long')">平仓</a-button>
          <a-button size="small" :disabled="!hasCloseActivity('long')" title="撤销本方向平仓挂单并停止执行中的平仓算法" @click="stopCurrent('long')">停止平仓</a-button>
        </div>
      </div>
    </div>

    <div class="triggers">
      <div class="sec-title">挂单监控</div>
      <div v-if="!s.triggers.length" class="muted">无挂单</div>
      <div v-for="t in s.triggers" :key="t.trigger_id" class="row">
        <span>
          {{ ACTION_ZH[t.action] }}{{ t.is_close ? "（平仓）" : "" }}
          差价 {{ fmt(t.spread) }} × {{ t.lots }} 份
        </span>
        <a @click="cancelTrigger(t.trigger_id)">取消</a>
      </div>
    </div>

    <div class="legs">
      <LegCard title="A腿（主动·追价）" :state="legs.leg_a" />
      <LegCard title="B腿（被动·对冲）" :state="legs.leg_b" />
    </div>
  </div>
</template>

<style scoped>
.panel { padding: 8px; }
.lots-bar { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
.muted { color: #999; font-size: 12px; }
.zones { display: flex; gap: 12px; }
.zone { flex: 1; border: 1px solid #f0f0f0; border-radius: 6px; padding: 8px; }
.zone-title { font-weight: 600; margin-bottom: 6px; }
.zone-title.short { color: #cf1322; }
.zone-title.long { color: #389e0d; }
.line { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; flex-wrap: wrap; }
.cur { font-size: 12px; color: #389e0d; white-space: nowrap; }
.cur.off { color: #999; }
.stale { font-size: 11px; color: #d46b08; white-space: nowrap; }
.line label { width: 62px; font-size: 12px; color: #666; }
.triggers { margin-top: 10px; }
.sec-title { font-size: 12px; color: #999; margin-bottom: 4px; }
.row { display: flex; justify-content: space-between; font-size: 13px; line-height: 1.9; }
.legs { display: flex; gap: 8px; margin-top: 10px; }
</style>
