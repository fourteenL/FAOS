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

// 当前策略存活触发单的动作集合（有挂单的字段保持下单价格，直到该单停止/触发）
const liveActions = computed(() => new Set((s.value?.triggers || []).map((t) => t.action)));


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
const stopDisabled = computed(() => {
      const st = s.value;
      return !st || (st.triggers.length === 0 && st.active_algos.length === 0);
    });
const openTrigger = computed(() => s.value?.triggers.find((t) => t.action.startsWith("open_")));
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

// 启动：只做首次挂单；已有未触发的开仓挂单（任一方向）提示改用「修改」
async function start(action) {
  const spread = form[OPEN_INPUT[action]];
  if (spread == null) {
    message.warning("请填写触发差价");
    return;
  }
  if (openTrigger.value) {
    const which = openTrigger.value.action === "open_short" ? "做空" : "做多";
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

async function stopCurrent() {
  try {
    await store.stopStrategy(s.value.id);
    message.success("已停止当前操作（撤触发单 + 停算法），持仓保留");
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
          <a-button size="small" :disabled="stopDisabled" @click="stopCurrent">停止</a-button>
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
          <a-button size="small" type="primary" @click="start('open_long')">启动</a-button>
          <a-button size="small" @click="modify('open_long')">修改</a-button>
          <a-button size="small" :disabled="stopDisabled" @click="stopCurrent">停止</a-button>
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
          {{ { open_short: "做空开", open_long: "做多开", close_short: "空平", close_long: "多平" }[t.action] }}
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
.line { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }
.line label { width: 62px; font-size: 12px; color: #666; }
.triggers { margin-top: 10px; }
.sec-title { font-size: 12px; color: #999; margin-bottom: 4px; }
.row { display: flex; justify-content: space-between; font-size: 13px; line-height: 1.9; }
.legs { display: flex; gap: 8px; margin-top: 10px; }
</style>
