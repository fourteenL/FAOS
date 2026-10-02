<script setup>
import { computed, reactive, ref, watch } from "vue";
import { message } from "ant-design-vue";
import { useAppStore } from "../stores/app.js";

const store = useAppStore();
const s = computed(() => store.selected);

const ORDER_TYPES = ["对手价", "排队价", "最新价", "市价"];
const form = reactive({ slip_a: 5, slip_b: 5, order_type: "对手价" });

watch(
  () => s.value?.id,
  () => {
    if (!s.value) return;
    form.slip_a = s.value.slip_a ?? 5;
    form.slip_b = s.value.slip_b ?? 5;
    form.order_type = s.value.order_type ?? "对手价";
  },
  { immediate: true }
);

async function saveConfig() {
  try {
    await store.setConfig(s.value.id, {
      slip_a: form.slip_a, slip_b: form.slip_b, order_type: form.order_type,
    });
    message.success("参数已保存");
  } catch (e) {
    message.error(String(e));
  }
}

async function stopDirection(side) {
  try {
    await store.stopDirection(s.value.id, side);
    message.success(side === "short" ? "做空侧已停止（撤单+停算法）" : "做多侧已停止（撤单+停算法）");
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
        <span class="sid">策略号 #{{ s.id }}</span>
        <span class="pair">{{ s.pair }}</span>
        <span class="muted">净持仓 {{ Math.abs(s.held_units) }} 份（{{ s.held_units < 0 ? "空头价差" : s.held_units > 0 ? "多头价差" : "无" }}）</span>
      </div>

      <div class="row2">
        <a-popconfirm title="删除该策略并退订行情？" @confirm="removeStrategy">
          <a-button danger size="small">行情删除</a-button>
        </a-popconfirm>
        <a-button size="small" @click="stopDirection('short')">做空停止</a-button>
        <a-button size="small" @click="stopDirection('long')">做多停止</a-button>
      </div>

      <a-divider orientation="left" plain class="div">交易参数</a-divider>
      <div class="grid">
        <div class="field">
          <span>A腿滑点（跳）</span>
          <a-input-number v-model:value="form.slip_a" :min="0" size="small" style="width: 90px" />
        </div>
        <div class="field">
          <span>B腿滑点（跳）</span>
          <a-input-number v-model:value="form.slip_b" :min="0" size="small" style="width: 90px" />
        </div>
        <div class="field">
          <span>报单类型</span>
          <a-select v-model:value="form.order_type" size="small" style="width: 110px" :options="ORDER_TYPES.map((v) => ({ value: v, label: v }))" />
        </div>
        <a-button size="small" type="primary" @click="saveConfig">保存参数</a-button>
      </div>
      <p class="hint">市价类型以「对手价 + 滑点」限价实现；排队价不加滑点，成交速度可能变慢。</p>
    </template>
    <div v-else class="placeholder">点击上方表格选择一个策略后在此管理</div>
  </div>
</template>

<style scoped>
.admin { padding: 8px; }
.head { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.sid { font-weight: 700; font-size: 15px; }
.pair { font-family: monospace; }
.muted { color: #999; font-size: 12px; }
.row2 { display: flex; gap: 8px; margin: 10px 0 4px; }
.div { margin: 10px 0 6px; font-size: 12px; }
.grid { display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap; }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: #666; }
.hint { color: #bbb; font-size: 12px; margin-top: 8px; }
.placeholder { padding: 16px; color: #999; }
</style>
