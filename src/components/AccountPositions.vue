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
  <span>
    <a-button size="small" @click="toggle">持仓</a-button>
    <div v-if="open" class="fpanel" @click.self="open = false">
      <div class="fpanel-card">
        <div class="fpanel-head">
          <span>账户持仓（柜台实际 · 含双向今昨）</span>
          <a class="close" @click="open = false">×</a>
        </div>
        <div v-if="rows.length" class="fpanel-body">
        <div class="ap-body">
          <div v-for="r in rows" :key="r.sym" class="ap-row">
            <span class="mono">{{ r.sym }}</span>
            <span :class="{ hl: r.long_today || r.long_yd }">多 今{{ r.long_today }}/{{ r.long_yd }}昨</span>
            <span :class="{ hl: r.short_today || r.short_yd }">空 今{{ r.short_today }}/{{ r.short_yd }}昨</span>
          </div>
        </div>
        </div>
        <div v-else class="ap-empty">无持仓或未连接<br>锁仓隔离下同合约多空并存属正常，对冲发生在平仓时</div>
      </div>
    </div>
  </span>
</template>

<style scoped>
.fpanel-body { overflow-y: auto; flex: 1 1 auto; min-height: 0; }
.ap-body { display: flex; flex-direction: column; gap: 3px; }
.ap-row { display: flex; gap: 12px; font-size: 12px; }
.ap-row .hl { color: #cf1322; font-weight: 600; }
.ap-empty { color: #999; font-size: 12px; line-height: 1.6; }
.mono { font-family: Consolas, monospace; }
/* 与其余顶栏弹出框同风格（fpanel 全局样式在 App.vue） */
.fpanel { position: fixed; inset: 0; z-index: 1000; background: rgba(0,0,0,.45);
  display: flex; align-items: flex-start; justify-content: center; padding-top: 56px; }
.fpanel-card { width: 440px; max-height: 70vh; overflow: hidden;
    background: #fff; border-radius: 8px; box-shadow: 0 6px 24px rgba(0,0,0,.18); padding: 12px 14px;
  display: flex; flex-direction: column; }
.fpanel-head { display: flex; justify-content: space-between; font-weight: 600; font-size: 14px;
  margin-bottom: 8px; flex-shrink: 0; padding-bottom: 8px; border-bottom: 1px solid #f0f0f0; }
.close { font-size: 18px; color: #999; line-height: 1; cursor: pointer; }
</style>
