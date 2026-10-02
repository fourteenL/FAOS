<script setup>
defineProps({
  title: String,
  state: { type: Object, default: null },
});

function fmt(v) {
  return v == null ? "--" : Number(v).toFixed(2);
}
const STATUS_ZH = {
  SUBMITTING: "提交中", NOTTRADED: "未成交", PARTTRADED: "部分成交",
  ALLTRADED: "全部成交", CANCELLED: "已撤销", REJECTED: "拒单",
};

function statusText(o) {
  return o.status_zh || STATUS_ZH[String(o.status || "").toUpperCase()] || o.status || "--";
}
</script>

<template>
  <a-card size="small" :title="title" class="leg-card">
    <div class="sym">{{ state?.symbol || "--" }}</div>
    <div v-if="state?.chasing" class="chasing">{{ state.chasing }}</div>
    <div class="row"><span>多头持仓</span><b>{{ state?.long_pos ?? "--" }}</b></div>
    <div class="row"><span>空头持仓</span><b>{{ state?.short_pos ?? "--" }}</b></div>
    <div class="sec">当前委托</div>
    <div v-if="!state?.orders?.length" class="muted">无</div>
    <div v-for="o in state?.orders || []" :key="o.vt_orderid" class="row">
      <span>{{ o.direction === "long" ? "买" : "卖" }} {{ fmt(o.price) }} × {{ o.volume }}</span>
      <span class="muted">已成交 {{ o.traded }}｜{{ statusText(o) }}</span>
    </div>
    <div class="sec">最近成交</div>
    <div v-if="!state?.trades_recent?.length" class="muted">无</div>
    <div v-for="t in state?.trades_recent || []" :key="t.vt_tradeid" class="row">
      <span>{{ t.direction === "long" ? "买" : "卖" }} {{ fmt(t.price) }} × {{ t.volume }}</span>
    </div>
  </a-card>
</template>

<style scoped>
.leg-card { flex: 1 1 0; min-width: 0; }
.sym { font-weight: 600; margin-bottom: 4px; }
.chasing { color: #fa8c16; font-size: 12px; margin-bottom: 4px; }
.row { display: flex; justify-content: space-between; font-size: 12px; line-height: 1.9; }
.sec { font-size: 12px; color: #999; margin-top: 6px; border-top: 1px dashed #f0f0f0; padding-top: 4px; }
.muted { color: #bbb; font-size: 12px; }
</style>
