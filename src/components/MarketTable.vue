<script setup>
import { computed, onUnmounted, ref, watch } from "vue";
import { message } from "ant-design-vue";
import { useAppStore } from "../stores/app.js";
import { marketClock } from "../utils/market_open.js";
import DragSortModal from "./DragSortModal.vue";

const emit = defineEmits(["select"]);
const store = useAppStore();
const market = marketClock();  // 交易时段指示：开盘策略号绿/休市红（每 30 秒刷新）
onUnmounted(market.stop);

// 方向标签：平仓优先识别（2026-10-13 用户定稿：持空+多开单=平仓中，不再显示歧义的"空多"）
function directionOf(row) {
  const held = row.held_units || 0;
  const armed = (action) => (row.triggers || []).some((t) => t.action === action);
  const exec = row.executing_action;
  if (held < 0 && (armed("open_long") || exec === "open_long"))
    return { label: "做空·平仓中", cls: "dir-short" };
  if (held > 0 && (armed("open_short") || exec === "open_short"))
    return { label: "做多·平仓中", cls: "dir-long" };
  const hasLong = held > 0 || armed("open_long") || exec === "open_long";
  const hasShort = held < 0 || armed("open_short") || exec === "open_short";
  if (hasLong && hasShort) return { label: "空多", cls: "dir-both" };
  if (hasShort) return { label: "做空", cls: "dir-short" };
  if (hasLong) return { label: "做多", cls: "dir-long" };
  return { label: "空闲", cls: "" };  // 2026-10-15 定稿：做多绿/做空红/空闲无色
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

// 排序：自定义（弹窗拖动列表调序）/ 合约升序 / 合约降序（2026-10-16 定稿）
const sortMode = ref("custom");
const sortModal = ref(null);

function openSortModal() {
  sortModal.value?.open(rows.value);
}

async function saveSortOrder(orderedIds) {
  try {
    await store.reorderStrategies(orderedIds);
    sortMode.value = "custom";  // 保存后表格按自定义顺序渲染
    message.success("顺序已保存");
  } catch (e) {
    message.error("排序保存失败: " + e);
  }
}
const rows = computed(() => {
  const list = [...store.visibleStrategies];
  if (sortMode.value === "asc")
    list.sort((a, b) => String(a.leg_a).localeCompare(b.leg_a) || a.id - b.id);
  else if (sortMode.value === "desc")
    list.sort((a, b) => String(b.leg_a).localeCompare(a.leg_a) || b.id - a.id);
  return list;  // custom：store 已按 sort_key 排
});

</script>

<template>
  <div class="mt-root">
  <div class="bar">
    <span class="muted">排序</span>
    <button :class="{ tg: true, on: sortMode === 'custom' }" @click="sortMode = 'custom'">自定义</button>
    <button :class="{ tg: true, on: sortMode === 'asc' }" @click="sortMode = 'asc'">合约↑</button>
    <button :class="{ tg: true, on: sortMode === 'desc' }" @click="sortMode = 'desc'">合约↓</button>
    <a-button size="small" @click="openSortModal">调序</a-button>
  </div>
  <a-empty v-if="!store.visibleStrategies.length" description="暂无订阅的策略，请在「订阅管理」中创建并订阅" />
  <a-table
    v-else
    :columns="columns"
    :data-source="rows"
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
    <DragSortModal ref="sortModal" @save="saveSortOrder" />
  </div>
</template>


<style scoped>
.up { color: #cf1322; font-weight: 600; }
.down { color: #389e0d; font-weight: 600; }
.dir-long { color: #389e0d; font-weight: 600; }   /* 做多=绿 */
.dir-short { color: #cf1322; font-weight: 600; }  /* 做空=红 */
.dir-both { color: #1677ff; font-weight: 600; }   /* 空多（双向）=蓝 */
.pos-hold { color: #cf1322; font-weight: 600; }  /* 仓位非零标红 */
.mkt-open { color: #389e0d; font-weight: 600; }   /* 交易时段：策略号绿 */
.mkt-closed { color: #cf1322; font-weight: 600; } /* 休市：策略号红 */
:deep(td) { font-variant-numeric: tabular-nums; }  /* 等宽数字：位数变化不改变单元格宽度 */
:deep(.ant-table-thead > tr > th) {                  /* 表头吸顶：滚动时列头固定 */
  position: sticky; top: 0; z-index: 2;
  background: #fafafa; box-shadow: 0 1px 0 #f0f0f0;
}
.bar { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }
.tg { border: 1px solid #d9d9d9; background: #fff; border-radius: 4px; font-size: 12px;
  padding: 1px 8px; cursor: pointer; }
.tg.on { color: #1677ff; border-color: #1677ff; }
.mt-root { min-height: 0; }
.idcell { display: inline-flex; gap: 2px; align-items: center; }
.drag-hd { cursor: grab; color: #999; font-size: 13px; line-height: 1; user-select: none; }
.drag-hd:active { cursor: grabbing; }
</style>
