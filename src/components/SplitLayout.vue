<script setup>
import { ref } from "vue";

const pct = ref(55); // 顶部占比
const container = ref(null);
const dragging = ref(false);

function onDown(e) {
  dragging.value = true;
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}
function onMove(e) {
  if (!dragging.value || !container.value) return;
  const rect = container.value.getBoundingClientRect();
  const p = ((e.clientY - rect.top) / rect.height) * 100;
  pct.value = Math.min(80, Math.max(20, p));
}
function onUp() {
  dragging.value = false;
  document.removeEventListener("mousemove", onMove);
  document.removeEventListener("mouseup", onUp);
}
</script>

<template>
  <div ref="container" class="split">
    <div class="top" :style="{ flexBasis: pct + '%' }"><slot name="top" /></div>
    <div class="bar" @mousedown="onDown" />
    <div class="bottom"><slot name="bottom" /></div>
  </div>
</template>

<style scoped>
.split { display: flex; flex-direction: column; height: 100%; }
.top { flex: 0 0 auto; overflow: auto; min-height: 0; }
.bar { height: 6px; background: #f0f0f0; cursor: row-resize; }
.bar:hover { background: #d6e4ff; }
.bottom { flex: 1 1 auto; overflow: auto; min-height: 0; display: flex; }
</style>
