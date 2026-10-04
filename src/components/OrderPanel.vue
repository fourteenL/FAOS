<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import { message } from "ant-design-vue";
import { useAppStore, tickPrecision } from "../stores/app.js";
import LegCard from "./LegCard.vue";

const store = useAppStore();
const form = reactive({ open_short: null, close_short: null, open_long: null, close_long: null });
const legs = ref({ leg_a: null, leg_b: null });

const s = computed(() => store.selected);

watch(
  () => s.value?.id,
  () => {
    if (!s.value) return;
    refreshLegs();
  },
  { immediate: true }
);

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

// 点击行情单元格 → 填入对应输入框（immediate：面板可能晚于点击挂载）
watch(
  () => store.fillRequest,
  (req) => {
    if (!req) return;
    if (req.action === "all") {
      const vs = req.values || {};
      for (const k of ["open_short", "close_short", "open_long", "close_long"]) {
        if (vs[k] != null) form[k] = Number(Number(vs[k]).toFixed(precision.value));
      }
      return;
    }
  },
  { immediate: true }
);

// 无触发单且无运行算法 → 无可停止的活动，停止按钮置灰
const stopDisabled = computed(() => {
      const st = s.value;
      return !st || (st.triggers.length === 0 && st.active_algos.length === 0);
    });
const hasShort = computed(() => (s.value?.held_units ?? 0) < 0);
const hasLong = computed(() => (s.value?.held_units ?? 0) > 0);
const openTrigger = computed(() => s.value?.triggers.find((t) => t.action.startsWith("open_")));
const closeShortTrigger = computed(() => s.value?.triggers.find((t) => t.action === "close_short"));
const closeLongTrigger = computed(() => s.value?.triggers.find((t) => t.action === "close_long"));

async function place(action, spread, lots) {
  if (spread == null) {
    message.warning("请填写触发差价");
    return;
  }
  try {
    if (action === "open_short" || action === "open_long") {
      // 重启式下单：先停止当前策略（撤触发单+停算法，持仓保留），再按新价格挂单
      await store.stopStrategy(s.value.id);
    }
    await store.placeTrigger(s.value.id, { action, spread, lots: lots ?? s.value.single_lots });
    message.success("触发单已挂出，等待行情触发");
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
          <a-input-number v-model:value="form.open_short" :min="0" :step="step" :precision="precision" size="small" style="width: 90px" />
          <a-button size="small" type="primary" danger
            @click="place('open_short', form.open_short)">做空</a-button>
        </div>
        <div class="line">
          <label>空平差价</label>
          <a-input-number v-model:value="form.close_short" :min="0" :step="step" :precision="precision" size="small" style="width: 90px" />
          <a-button size="small" :disabled="!hasShort || !!closeShortTrigger"
            @click="place('close_short', form.close_short)">空平</a-button>
          <a-button size="small" :disabled="stopDisabled" @click="stopCurrent">停止</a-button>
        </div>
      </div>
      <div class="zone">
        <div class="zone-title long">做多（买A卖B）</div>
        <div class="line">
          <label>多开差价</label>
          <a-input-number v-model:value="form.open_long" :min="0" :step="step" :precision="precision" size="small" style="width: 90px" />
          <a-button size="small" type="primary"
            @click="place('open_long', form.open_long)">做多</a-button>
        </div>
        <div class="line">
          <label>多平差价</label>
          <a-input-number v-model:value="form.close_long" :min="0" :step="step" :precision="precision" size="small" style="width: 90px" />
          <a-button size="small" :disabled="!hasLong || !!closeLongTrigger"
            @click="place('close_long', form.close_long)">多平</a-button>
          <a-button size="small" :disabled="stopDisabled" @click="stopCurrent">停止</a-button>
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
