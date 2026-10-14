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

// 平仓=反向启动（空平/多平按钮已移除），开/平两框不再有比较对象——不做倒挂校验：
// 用户自定触发价（如填高价尽快触发平仓）是合法意图
async function doPlace(action, spread) {
  const companion = action === "open_short" ? form.close_short
    : action === "open_long" ? form.close_long : null;  // 开仓时对应平仓价一并保存
  try {
    await store.placeTrigger(s.value.id, {
      action, spread, lots: s.value.single_lots,
      companion_spread: companion ?? undefined,
    });
    dirty.delete(action);  // 下单确认：该字段恢复跟随（显示挂单价）
    message.success("触发单已挂出（本地监控，等差价触发后拆腿下单）");
  } catch (e) {
    message.error(String(e));
  }
}

// 平仓=反向开仓：持反向仓时空框提示给平仓指引（2026-10-13 实测用户卡在空框无日志可查）
function emptyInputHint(action) {
  const held = s.value?.held_units || 0;
  if (action === "open_long" && held < 0)
    return "持空仓：请填多开差价平仓（当前多开价附近可尽快触发）";
  if (action === "open_short" && held > 0)
    return "持多仓：请填空开差价平仓（当前空开价附近可尽快触发）";
  return null;
}

// 启动：每方向一张开仓挂单（做空/做多可并存）；同方向已有未触发挂单提示改用「修改」
async function start(action) {
  const spread = form[OPEN_INPUT[action]];
  if (spread == null) {
    message.warning(emptyInputHint(action) || "请填写触发差价");
    return;
  }
  if (openTriggerOf(action)) {
    const which = action === "open_short" ? "做空" : "做多";
    message.warning(`已有未触发的${which}开仓挂单，请用「修改」改价或先「停止」`);
    return;
  }
  await doPlace(action, spread);
}

// 修改：改价 = 撤掉本方向旧挂单后按输入框新价重挂；无挂单时等同启动
async function modify(action) {
  const spread = form[OPEN_INPUT[action]];
  if (spread == null) {
    message.warning("请填写触发差价");
    return;
  }
  const old = openTriggerOf(action);
  try {
    if (old) await store.cancelTrigger(s.value.id, old.trigger_id);
    await doPlace(action, spread);
    if (old) message.success("已按新价格重新挂单");
  } catch (e) {
    message.error(String(e));
  }
}

// 平仓=反向开仓（用户定稿 2026-10-14 再确认：持多仓去做空区启动、持空仓去做多区启动，
// 界面不加平仓按钮）；空平/多平框仅作伴随价记录与参考显示。

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

// 平仓=反向开仓（用户定稿 2026-10-13）：持仓为反向的开仓挂单标注（平仓）
const isClosingOrder = (t) => {
  const held = s.value?.held_units || 0;
  return (held < 0 && t.action === "open_long") || (held > 0 && t.action === "open_short");
};
</script>

<template>
  <div v-if="s" class="panel">
    <div class="zones">
      <div class="zone">
        <div class="zone-title short">做空（卖A买B）</div>
        <div class="line">
          <label>空开差价</label>
          <a-input-number v-model:value="form.open_short" :step="step" :precision="precision" size="small" style="width: 90px" @change="markDirty('open_short')" />
          <span class="cur" :class="{ off: !cur?.live }" title="实时空开价差（卖A买B 立即成交价）——做空触发条件：空开 ≥ 填价">当前 {{ fmtLive(cur?.short_open) }}</span>
          <a-button size="small" type="primary" danger @click="start('open_short')">启动</a-button>
          <a-button size="small" @click="modify('open_short')">修改</a-button>
          <a-button size="small" :disabled="stopDisabled('short')" @click="stopCurrent('short')">停止</a-button>
        </div>
        <div class="line">
          <label>空平差价</label>
          <a-input-number v-model:value="form.close_short" :step="step" :precision="precision" size="small" style="width: 90px" @change="markDirty('close_short')" />
        </div>
      </div>
      <div class="zone">
        <div class="zone-title long">做多（买A卖B）</div>
        <div class="line">
          <label>多开差价</label>
          <a-input-number v-model:value="form.open_long" :step="step" :precision="precision" size="small" style="width: 90px" @change="markDirty('open_long')" />
          <span class="cur" :class="{ off: !cur?.live }" title="实时多开价差（买A卖B 立即成交价）——做多触发条件：多开 ≤ 填价">当前 {{ fmtLive(cur?.long_open) }}</span>
          <a-button size="small" type="primary" @click="start('open_long')">启动</a-button>
          <a-button size="small" @click="modify('open_long')">修改</a-button>
          <a-button size="small" :disabled="stopDisabled('long')" @click="stopCurrent('long')">停止</a-button>
        </div>
        <div class="line">
          <label>多平差价</label>
          <a-input-number v-model:value="form.close_long" :step="step" :precision="precision" size="small" style="width: 90px" @change="markDirty('close_long')" />
        </div>
      </div>
    </div>

    <div class="triggers">
      <div class="sec-title">挂单监控</div>
      <div v-if="!s.triggers.length" class="muted">无挂单</div>
      <div v-for="t in s.triggers" :key="t.trigger_id" class="row">
        <span>
          {{ ACTION_ZH[t.action] }}{{ isClosingOrder(t) ? "（平仓）" : "" }}
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
.line label { width: 62px; font-size: 12px; color: #666; }
.triggers { margin-top: 10px; }
.sec-title { font-size: 12px; color: #999; margin-bottom: 4px; }
.row { display: flex; justify-content: space-between; font-size: 13px; line-height: 1.9; }
.legs { display: flex; gap: 8px; margin-top: 10px; }
</style>
