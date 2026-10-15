<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useAppStore } from "../stores/app.js";

const store = useAppStore();
const open = ref(false);
let timer = null;
onMounted(() => {
  store.refreshAccountPositions();
  timer = setInterval(() => store.refreshAccountPositions(), 10000);
});
onUnmounted(() => clearInterval(timer));

// 账户真实持仓（柜台实际，含锁仓双向、今昨拆分）——与无限易所见一致
const rows = computed(() =>
  Object.entries(store.accountPositions)
    .map(([sym, v]) => ({ sym, ...v }))
    .sort((a, b) => a.sym.localeCompare(b.sym)));

function toggle() {
  open.value = !open.value;
  if (open.value) store.refreshAccountPositions();
}
</script>

<template>
  <span class="ap-wrap">
    <a-button size="small" @click="toggle">持仓</a-button>
    <div v-if="open" class="ap-panel">
      <div class="ap-head">
        <span>账户持仓（柜台实际 · 含双向今昨）</span>
        <a class="close" @click="open = false">×</a>
      </div>
      <div v-if="rows.length" class="ap-body">
        <div v-for="r in rows" :key="r.sym" class="ap-row">
          <span class="mono">{{ r.sym }}</span>
          <span :class="{ hl: r.long_today || r.long_yd }">多 今{{ r.long_today }}/{{ r.long_yd }}昨</span>
          <span :class="{ hl: r.short_today || r.short_yd }">空 今{{ r.short_today }}/{{ r.short_yd }}昨</span>
        </div>
      </div>
      <div v-else class="ap-empty">无持仓或未连接<br>锁仓隔离下同合约多空并存属正常，对冲发生在平仓时</div>
    </div>
  </span>
</template>

<style scoped>
.ap-wrap { position: relative; display: inline-block; }
.ap-panel { position: absolute; top: 30px; left: 0; z-index: 100; width: 300px;
  background: #fff; border: 1px solid #d9d9d9; border-radius: 6px;
  box-shadow: 0 4px 16px rgba(0,0,0,.15); padding: 8px 10px; }
.ap-head { display: flex; justify-content: space-between; font-weight: 600; font-size: 12px; margin-bottom: 6px; }
.close { color: #999; cursor: pointer; }
.ap-body { display: flex; flex-direction: column; gap: 3px; max-height: 320px; overflow-y: auto; }
.ap-row { display: flex; gap: 12px; font-size: 12px; }
.ap-row .hl { color: #cf1322; font-weight: 600; }
.ap-empty { color: #999; font-size: 12px; line-height: 1.6; }
</style>
