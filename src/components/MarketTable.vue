<script setup>
import { computed, onUnmounted, ref, watch } from "vue";
import { useAppStore } from "../stores/app.js";
import { marketClock } from "../utils/market_open.js";

const emit = defineEmits(["select"]);
const store = useAppStore();
const market = marketClock();  // 交易时段指示：开盘策略号绿/休市红（每 30 秒刷新）
onUnmounted(market.stop);

// 方向标签：持仓 ∨ 已挂开仓单 ∨ 执行中（挂单→执行→成交全程方向可见——用户定稿）
function directionOf(row) {
  const held = row.held_units || 0;
  const armed = (action) => (row.triggers || []).some((t) => t.action === action);
  const exec = row.executing_action;
  const hasLong = held > 0 || armed("open_long") || exec === "open_long";
  const hasShort = held < 0 || armed("open_short") || exec === "open_short";
  if (hasLong && hasShort) return { label: "空多", cls: "dir-active" };
  if (hasShort) return { label: "做空", cls: "dir-active" };
  if (hasLong) return { label: "做多", cls: "dir-active" };
  return { label: "空闲", cls: "dir-free" };
}

// 行高亮：执行中（浅红）> 挂单中（浅金）> 选中（浅蓝）
function rowBg(row) {
  if ((row.active_algos || []).length || row.status === "executing") return "#fff1f0";
  if ((row.triggers || []).length) return "#fffbe6";
  if (store.selectedId === row.id) return "#e6f4ff";
  return "";
}

// 策略归属的腿仓位：held_units × 单份数 推导两腿持仓（仅本行下单产生的仓位在此行显示）
function legPos(row, leg, dir) {
  const held = row.held_units || 0;
  const vol = Math.abs(held) * (row.single_lots || 1);
  if (held < 0) { // 做空价差：A 空头 + B 多头
    return (leg === "a" && dir === "short") || (leg === "b" && dir === "long") ? vol : 0;
  }
  if (held > 0) { // 做多价差：A 多头 + B 空头
    return (leg === "a" && dir === "long") || (leg === "b" && dir === "short") ? vol : 0;
  }
  return 0;
}

// 仓位单元格：非零标红加粗，零仓显示 --
function posCell(row, leg, dir) {
  const v = legPos(row, leg, dir);
  return { text: v > 0 ? v : "--", hold: v > 0 };
}

function fmt(v) {
  return v == null ? "--" : Number(v).toFixed(2);
}

// 四向差价列 = 策略保存的人工价（行情推送退役，差价手动填入）
function manualOf(row, key) {
  return row[key] == null ? null : row[key];
}

// 全列定宽 + tableLayout=fixed：差价/仓位内容变化（单帧尖刺、涨跌加粗）不再引起整表重排
const columns = computed(() => [
  { title: "编号", dataIndex: "id", key: "id", width: 60, align: "center" },
  { title: "A合约", dataIndex: "leg_a", key: "leg_a", width: 78, align: "center" },
  { title: "B合约", dataIndex: "leg_b", key: "leg_b", width: 78, align: "center" },
  { title: "A撤单", key: "cancel_a", width: 80, align: "center" },
  { title: "空开", key: "short_open", width: 76, align: "center" },
  { title: "空平", key: "short_cover", width: 76, align: "center" },
  { title: "A空仓", key: "a_short", width: 64, align: "center" },
  { title: "B空仓", key: "b_short", width: 64, align: "center" },
  { title: "多开", key: "long_open", width: 76, align: "center" },
  { title: "多平", key: "long_cover", width: 76, align: "center" },
  { title: "A多仓", key: "a_long", width: 64, align: "center" },
  { title: "B多仓", key: "b_long", width: 64, align: "center" },
  { title: "状态", key: "status", width: 130, align: "center" },
  { title: "单份", dataIndex: "single_lots", key: "single_lots", align: "center", width: 70 },
  { title: "总份", dataIndex: "total_lots", key: "total_lots", align: "center", width: 70 },
]);

function onRow(row) {
  return {
    onClick: () => {
      store.select(row.id);
      emit("select", row.id);
    },
    style: { cursor: "pointer", background: rowBg(row) },
  };
}

function cancelCountOf(row) {
  return store.cancelCount(row.pair, `${row.leg_a}.SHFE`);
}

</script>

<template>
  <a-empty v-if="!store.visibleStrategies.length" description="暂无订阅的策略，请在「订阅管理」中创建并订阅" />
  <a-table
    v-else
    :columns="columns"
    :data-source="store.visibleStrategies"
    :pagination="false"
    size="small"
    row-key="id"
    table-layout="fixed"
    :custom-row="onRow"
  >
    <template #headerCell="{ column }">
      <span>{{ column.title }}</span>
    </template>
    <template #bodyCell="{ column, record }">
      <template v-if="column.key === 'cancel_a'">{{ cancelCountOf(record) }}</template>
      <template v-else-if="column.key === 'id'">
        <span :class="market.value ? 'mkt-open' : 'mkt-closed'">#{{ record.id }}</span>
      </template>
      <template v-else-if="column.key === 'short_open'">
        <span>{{ fmt(manualOf(record, "manual_open_short")) }}</span>
      </template>
      <template v-else-if="column.key === 'short_cover'">
        <span>{{ fmt(manualOf(record, "manual_close_short")) }}</span>
      </template>
      <template v-else-if="column.key === 'long_open'">
        <span>{{ fmt(manualOf(record, "manual_open_long")) }}</span>
      </template>
      <template v-else-if="column.key === 'long_cover'">
        <span>{{ fmt(manualOf(record, "manual_close_long")) }}</span>
      </template>
      <template v-else-if="column.key === 'a_short'">
        <span :class="{ 'pos-hold': posCell(record, 'a', 'short').hold }">{{ posCell(record, 'a', 'short').text }}</span>
      </template>
      <template v-else-if="column.key === 'b_short'">
        <span :class="{ 'pos-hold': posCell(record, 'b', 'short').hold }">{{ posCell(record, 'b', 'short').text }}</span>
      </template>
      <template v-else-if="column.key === 'a_long'">
        <span :class="{ 'pos-hold': posCell(record, 'a', 'long').hold }">{{ posCell(record, 'a', 'long').text }}</span>
      </template>
      <template v-else-if="column.key === 'b_long'">
        <span :class="{ 'pos-hold': posCell(record, 'b', 'long').hold }">{{ posCell(record, 'b', 'long').text }}</span>
      </template>
      <template v-else-if="column.key === 'status'">
        <span :class="directionOf(record).cls">{{ directionOf(record).label }}</span>
      </template>
    </template>
  </a-table>
</template>

<style scoped>
.up { color: #cf1322; font-weight: 600; }
.down { color: #389e0d; font-weight: 600; }
.dir-free { color: #389e0d; font-weight: 600; }
.dir-active { color: #cf1322; font-weight: 600; }
.pos-hold { color: #cf1322; font-weight: 600; }  /* 仓位非零标红 */
.mkt-open { color: #389e0d; font-weight: 600; }   /* 交易时段：策略号绿 */
.mkt-closed { color: #cf1322; font-weight: 600; } /* 休市：策略号红 */
:deep(td) { font-variant-numeric: tabular-nums; }  /* 等宽数字：位数变化不改变单元格宽度 */
</style>
