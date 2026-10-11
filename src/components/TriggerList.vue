<script setup>
import { computed, ref } from "vue";
import { message } from "ant-design-vue";
import { useAppStore } from "../stores/app.js";

const store = useAppStore();
const visible = ref(false);

// 全策略活动触发单汇总：{策略号, 合约对, 方向, 触发价, 份数, trigger_id}
const rows = computed(() => {
  const out = [];
  for (const s of store.strategies) {
    for (const t of s.triggers || []) {
      out.push({
        id: s.id, pair: s.pair, action: t.action, spread: t.spread,
        lots: t.lots, trigger_id: t.trigger_id,
      });
    }
  }
  return out;
});

const ACTION_ZH = { open_short: "做空开", open_long: "做多开", close_short: "空平", close_long: "多平" };

async function cancel(row) {
  try {
    await store.cancelTrigger(row.id, row.trigger_id);
    message.success("已取消");
  } catch (e) {
    message.error(String(e));
  }
}

function open() {
  visible.value = true;
}
defineExpose({ open });
</script>

<template>
  <div v-if="visible" class="fpanel" @click.self="visible = false">
    <div class="fpanel-card">
      <div class="fpanel-head">
        <span>活动触发单（{{ rows.length }}）</span>
        <a class="close" @click="visible = false">×</a>
      </div>
      <div v-if="!rows.length" class="muted">无活动触发单</div>
      <table v-else class="tlist">
        <thead>
          <tr><th>策略号</th><th>合约对</th><th>方向</th><th>触发价</th><th>份数</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.trigger_id">
            <td>#{{ r.id }}</td>
            <td class="mono">{{ r.pair }}</td>
            <td>{{ ACTION_ZH[r.action] || r.action }}</td>
            <td class="mono">{{ r.spread }}</td>
            <td>{{ r.lots }}</td>
            <td><a style="color: #cf1322" @click="cancel(r)">取消</a></td>
          </tr>
        </tbody>
      </table>
      <p class="hint">触发单为本地监控，等差价条件满足后自动拆腿下单（柜台在触发前不可见）。</p>
    </div>
  </div>
</template>

<style scoped>
.fpanel { position: fixed; inset: 0; z-index: 1000; background: rgba(0,0,0,.45);
  display: flex; align-items: flex-start; justify-content: center; padding-top: 56px; }
.fpanel-card { width: 520px; max-height: 70vh; overflow-y: auto;
  background: #fff; border-radius: 8px; box-shadow: 0 6px 24px rgba(0,0,0,.18); padding: 12px 14px; }
.fpanel-head { display: flex; justify-content: space-between; font-weight: 600; font-size: 14px; margin-bottom: 8px; }
.close { font-size: 18px; color: #999; line-height: 1; }
.muted { color: #999; font-size: 12px; }
.hint { color: #bbb; font-size: 12px; margin: 8px 0 0; }
.tlist { width: 100%; font-size: 12px; border-collapse: collapse; }
.tlist th, .tlist td { padding: 4px 6px; text-align: center; border-bottom: 1px solid #f5f5f5; }
.tlist th { color: #999; font-weight: 400; }
.mono { font-family: monospace; }
</style>
