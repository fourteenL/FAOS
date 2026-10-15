<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { message } from "ant-design-vue";
import { useAppStore } from "../stores/app.js";
import { marketClock } from "../utils/market_open.js";

const store = useAppStore();
const s = computed(() => store.selected);
const market = marketClock();
onUnmounted(market.stop);

const ORDER_TYPES = ["对手价", "挂价", "最新价", "市价"];
const form = reactive({ slip_a: 0, slip_b: 100, order_type_a: "挂价", order_type_b: "对手价" });
const risk = reactive({ max_cancels_a: 2000 });
const lots = reactive({ single: 1, total: 10 });
const legPos = ref(null);  // A/B 腿持仓快照（今昨拆分，10s 轮询）
// 平到目标：输入框预填当前持仓，改小的差额即平仓量（null=未同步，按钮前置空不可点）
const tgt = reactive({
  A: { long_today: null, long_yd: null, short_today: null, short_yd: null },
  B: { long_today: null, long_yd: null, short_today: null, short_yd: null },
});
const TGT_KEYS = ["long_today", "long_yd", "short_today", "short_yd"];
const tgtCache = {};  // strategy_id -> 编辑中的目标数（2026-10-14 定稿：输入绑定策略，切换跟手不串值）

function prefillTargets() {
  for (const leg of ["A", "B"]) {
    const snap = legPos.value?.[`leg_${leg.toLowerCase()}`];
    if (!snap) continue;
    for (const k of ["long_today", "long_yd", "short_today", "short_yd"]) {
      if (tgt[leg][k] == null) tgt[leg][k] = snap[k] ?? 0;  // 用户改过的值不覆盖
    }
  }
}

async function closeLeg(leg) {
  const t = tgt[leg];
  if (Object.values(t).some((v) => v == null)) {
    message.warning("持仓数据未同步，稍候再试");
    return;
  }
  try {
    const r = await store.closeLegTarget(s.value.id, leg, { ...t });
    const parts = (r.sent || []).map((x) => `${x.direction}${x.volume}手·${x.offset}`).join("、");
    message.success(`已发平仓委托：${parts || "无"}`);
    delete tgtCache[s.value.id];  // 平仓后该策略编辑值作废
    for (const k of Object.keys(t)) t[k] = null;  // 重置让下次轮询重新预填
    refreshLegs();
  } catch (e) {
    message.error(String(e));
  }
}

let legTimer = null;
async function refreshLegs() {
  if (!s.value) return;
  try {
    legPos.value = await store.refreshLegOrders(s.value.id);
    prefillTargets();
  } catch (e) {
    legPos.value = null;  /* CTP 未连接时保持空 */
  }
  store.refreshAccountPositions();  // 账户真实持仓（含双向）随同刷新
}
onMounted(() => {
  legTimer = setInterval(refreshLegs, 10000);
  store.refreshAccountPositions();
});

// 账户持仓簿（柜台实际）：锁仓隔离后同合约多空并存——真实可见，不误以为对冲掉
const accountRows = computed(() =>
  Object.entries(store.accountPositions)
    .map(([sym, v]) => ({ sym, ...v }))
    .sort((a, b) => a.sym.localeCompare(b.sym)));
onUnmounted(() => clearInterval(legTimer));

watch(
  () => s.value?.id,
  (newId, oldId) => {
    if (!s.value) return;
    form.slip_a = s.value.slip_a ?? 0;
    form.slip_b = s.value.slip_b ?? 100;
    form.order_type_a = s.value.order_type_a ?? "挂价";
    form.order_type_b = s.value.order_type_b ?? "对手价";
    risk.max_cancels_a = s.value.max_cancels_a ?? 2000;
    lots.single = s.value.single_lots ?? 1;
    lots.total = s.value.total_lots ?? 10;
    // 今昨仓目标输入绑定策略（2026-10-14 定稿）：存旧载新，编辑值不跨策略串
    if (oldId != null) tgtCache[oldId] = JSON.parse(JSON.stringify(tgt));
    const c = newId != null ? tgtCache[newId] : null;
    for (const leg of ["A", "B"]) {
      for (const k of TGT_KEYS) tgt[leg][k] = c ? c[leg][k] : null;
    }
    legPos.value = null;
    refreshLegs();
  },
  { immediate: true }
);

async function saveLots() {
  try {
    await store.setLots(s.value.id, { single_lots: lots.single, total_lots: lots.total });
    message.success("份数已保存");
  } catch (e) {
    message.error(String(e));
  }
}

async function saveConfig() {
  try {
    await store.setConfig(s.value.id, {
      slip_a: form.slip_a, slip_b: form.slip_b,
      order_type_a: form.order_type_a, order_type_b: form.order_type_b,
      max_cancels_a: risk.max_cancels_a,
    });
    message.success("参数已保存");
  } catch (e) {
    message.error(String(e));
  }
}

async function stopAll() {
  try {
    await store.stopStrategy(s.value.id);
    message.success("已停止（撤触发单 + 停算法），持仓保留");
  } catch (e) {
    message.error(String(e));
  }
}

async function removeStrategy() {
  try {
    await store.removeStrategy(s.value.id);
    message.success("已删除并退订行情");
  } catch (e) {
    message.error(String(e));
  }
}

</script>

<template>
  <div class="admin">
    <template v-if="s">
      <div class="head">
        <span class="sid" :class="market.value ? 'mkt-open' : 'mkt-closed'">策略号 #{{ s.id }}</span>
        <span class="pair">{{ s.pair }}</span>
        <span class="muted">净持仓 {{ Math.abs(s.held_units) }} 份（{{ s.held_units < 0 ? "空头价差" : s.held_units > 0 ? "多头价差" : "无" }}）</span>
      </div>

      <div class="row2">
        <a-popconfirm title="删除该策略并退订行情？" @confirm="removeStrategy">
          <a-button danger size="small">行情删除</a-button>
        </a-popconfirm>
        <a-button size="small" @click="stopAll">停止</a-button>
      </div>

      <a-divider orientation="left" plain class="div">交易参数（A/B 腿分别设置）</a-divider>
      <div class="grid">
        <div class="field">
          <span>A腿 报单类型</span>
          <a-select v-model:value="form.order_type_a" size="small" style="width: 100px" :options="ORDER_TYPES.map((v) => ({ value: v, label: v }))" />
        </div>
        <div class="field">
          <span>A腿滑点（跳）</span>
          <a-input-number v-model:value="form.slip_a" :min="0" size="small" style="width: 80px" />
        </div>
        <div class="field">
          <span>B腿 报单类型</span>
          <a-select v-model:value="form.order_type_b" size="small" style="width: 100px" :options="ORDER_TYPES.map((v) => ({ value: v, label: v }))" />
        </div>
        <div class="field">
          <span>B腿滑点（跳）</span>
          <a-input-number v-model:value="form.slip_b" :min="0" size="small" style="width: 80px" />
        </div>
        <div class="field">
          <span>A腿撤单上限（超限拒下单）</span>
          <a-input-number v-model:value="risk.max_cancels_a" :min="1" size="small" style="width: 90px" />
        </div>
        <a-button size="small" type="primary" @click="saveConfig">保存参数</a-button>
      </div>
      <p class="hint">A 腿默认挂价排队（滑点 0）；B 腿默认对手价 + 100 跳滑点对冲。市价以「对手价 + 滑点」限价实现。风控：A 腿累计撤单 {{ s.cancel_count_a }} / 上限 {{ risk.max_cancels_a }}，触限自动停止策略并撤全部挂单。</p>

      <a-divider orientation="left" plain class="div">账户持仓（柜台实际 · 含双向今昨）</a-divider>
      <div v-if="accountRows.length" class="acct">
        <div v-for="r in accountRows" :key="r.sym" class="acct-row">
          <span class="mono">{{ r.sym }}</span>
          <span :class="{ hl: r.long_today || r.long_yd }">多 今{{ r.long_today }}/{{ r.long_yd }}昨</span>
          <span :class="{ hl: r.short_today || r.short_yd }">空 今{{ r.short_today }}/{{ r.short_yd }}昨</span>
        </div>
      </div>
      <p v-else class="hint">（无持仓或未连接；锁仓隔离下同合约多空并存属正常，对冲发生在平仓时）</p>

      <a-divider orientation="left" plain class="div">A/B 腿持仓（今 / 昨）——改小目标数点「平到目标」</a-divider>
      <div class="grid">
        <div class="field">
          <span>A腿 {{ legPos?.leg_a?.symbol || "--" }}</span>
          <div class="leg-close">
            <span class="hint">多</span>
            今<a-input-number v-model:value="tgt.A.long_today" :min="0" size="small" style="width: 52px" />
            昨<a-input-number v-model:value="tgt.A.long_yd" :min="0" size="small" style="width: 52px" />
            <span class="hint">空</span>
            今<a-input-number v-model:value="tgt.A.short_today" :min="0" size="small" style="width: 52px" />
            昨<a-input-number v-model:value="tgt.A.short_yd" :min="0" size="small" style="width: 52px" />
            <a-button size="small" danger @click="closeLeg('A')">平到目标</a-button>
          </div>
        </div>
        <div class="field">
          <span>B腿 {{ legPos?.leg_b?.symbol || "--" }}</span>
          <div class="leg-close">
            <span class="hint">多</span>
            今<a-input-number v-model:value="tgt.B.long_today" :min="0" size="small" style="width: 52px" />
            昨<a-input-number v-model:value="tgt.B.long_yd" :min="0" size="small" style="width: 52px" />
            <span class="hint">空</span>
            今<a-input-number v-model:value="tgt.B.short_today" :min="0" size="small" style="width: 52px" />
            昨<a-input-number v-model:value="tgt.B.short_yd" :min="0" size="small" style="width: 52px" />
            <a-button size="small" danger @click="closeLeg('B')">平到目标</a-button>
          </div>
        </div>
      </div>
      <p class="hint">目标数预填当前持仓；改小多少平多少（对手价立即成交取向，今仓平今/昨仓平昨），只平不开。此为单腿手动管理，不经价差算法；平仓后账户与策略持仓差异会由登录对账提醒。</p>

      <a-divider orientation="left" plain class="div">下单份数</a-divider>
      <div class="grid">
        <div class="field">
          <span>单份（每次触发手数）</span>
          <a-input-number v-model:value="lots.single" :min="1" size="small" style="width: 90px" />
        </div>
        <div class="field">
          <span>总份（累计上限）</span>
          <a-input-number v-model:value="lots.total" :min="1" size="small" style="width: 90px" />
        </div>
        <a-button size="small" type="primary" @click="saveLots">保存份数</a-button>
      </div>
      <p class="hint">净持仓 {{ Math.abs(s.held_units) }} 份（{{ s.held_units < 0 ? "空头价差" : s.held_units > 0 ? "多头价差" : "无" }}），上限校验：开仓成交后 |净持仓| ≤ 总份（做空/做多可双向同时挂单，按净额校验）。</p>
    </template>
    <div v-else class="placeholder">点击上方表格选择一个策略后在此管理</div>
  </div>
</template>

<style scoped>
.admin { padding: 8px; }
.head { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.sid { font-weight: 700; font-size: 15px; }
.mkt-open { color: #389e0d; }
.mkt-closed { color: #cf1322; }
.pair { font-family: monospace; }
.muted { color: #999; font-size: 12px; }
.row2 { display: flex; gap: 8px; margin: 10px 0 4px; }
.div { margin: 10px 0 6px; font-size: 12px; }
.grid { display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap; }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: #666; }
.hint { color: #bbb; font-size: 12px; margin-top: 8px; }
.placeholder { padding: 16px; color: #999; }
.acct { display: flex; flex-direction: column; gap: 2px; }
.acct-row { display: flex; gap: 16px; font-size: 12px; }
.acct-row .hl { color: #cf1322; font-weight: 600; }
</style>
