<script setup>
import { ref } from "vue";
import { message } from "ant-design-vue";

// 自定义排序弹窗（2026-10-16 定稿：表格内拖动在 WebView 真机不可用——
// 弹出纯 div 列表用指针事件拖动，保存后表格按新序渲染）
const emit = defineEmits(["save"]);
const visible = ref(false);
const list = ref([]);          // 弹窗内可拖动的本地副本（拖动即时换序，保存才落库）
const draggingKey = ref(null);
let rootEl = null;

function open(strategies) {
  list.value = strategies.map((s) => ({ id: s.id, pair: s.pair, dir: dirOf(s) }));
  visible.value = true;
}
defineExpose({ open });

function dirOf(s) {
  const held = s.held_units || 0;
  if (held < 0) return "做空";
  if (held > 0) return "做多";
  return "空闲";
}

function setRoot(el) {
  rootEl = el;
}

// 指针拖动：整行即把手——mousedown 起拖、mousemove 跨行中点本地换序、mouseup 结束
function onItemDown(item, e) {
  e.preventDefault();
  draggingKey.value = item.id;
  window.addEventListener("mousemove", onDragMove);
  window.addEventListener("mouseup", onDragUp);
}

function onDragMove(e) {
  if (draggingKey.value == null || !rootEl) return;
  const items = [...rootEl.querySelectorAll(".ds-item")];
  if (!items.length) return;
  let targetIdx = items.length - 1;
  for (let i = 0; i < items.length; i++) {
    const r = items[i].getBoundingClientRect();
    if (e.clientY < r.top + r.height / 2) { targetIdx = i; break; }
  }
  const ids = list.value.map((x) => x.id);
  const from = ids.indexOf(draggingKey.value);
  if (from < 0 || from === targetIdx) return;
  const moved = list.value.splice(from, 1)[0];
  list.value.splice(targetIdx, 0, moved);  // 本地即时换序
}

function onDragUp() {
  draggingKey.value = null;
  window.removeEventListener("mousemove", onDragMove);
  window.removeEventListener("mouseup", onDragUp);
}

function save() {
  emit("save", list.value.map((x) => x.id));
  visible.value = false;
}

function cancel() {
  visible.value = false;
  message.info("已取消，顺序未保存");
}
</script>

<template>
  <div v-if="visible" class="ds-mask" @click.self="cancel">
    <div class="ds-card" :ref="setRoot">
      <div class="ds-head">
        <span>自定义排序（拖动调整，保存后生效）</span>
        <a class="ds-close" @click="cancel">×</a>
      </div>
      <div class="ds-list">
        <div v-for="(it, i) in list" :key="it.id" class="ds-item"
             :class="{ dragging: draggingKey === it.id }"
             @mousedown="onItemDown(it, $event)">
          <span class="ds-idx">{{ i + 1 }}</span>
          <span class="ds-pair">{{ it.pair }}</span>
          <span class="ds-dir">{{ it.dir }}</span>
          <span class="ds-grip">⠿</span>
        </div>
      </div>
      <div class="ds-foot">
        <a-button size="small" @click="cancel">取消</a-button>
        <a-button size="small" type="primary" @click="save">保存顺序</a-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ds-mask { position: fixed; inset: 0; z-index: 1000; background: rgba(0,0,0,.45);
  display: flex; align-items: center; justify-content: center; }
.ds-card { width: 360px; max-height: 70vh; display: flex; flex-direction: column;
  background: #fff; border-radius: 8px; box-shadow: 0 6px 24px rgba(0,0,0,.18); padding: 12px 14px; }
.ds-head { display: flex; justify-content: space-between; font-weight: 600; font-size: 13px; margin-bottom: 8px; }
.ds-close { color: #999; cursor: pointer; font-size: 16px; }
.ds-list { overflow-y: auto; display: flex; flex-direction: column; gap: 4px; min-height: 120px; }
.ds-item { display: flex; gap: 10px; align-items: center; padding: 4px 8px; border: 1px solid #f0f0f0;
  border-radius: 6px; font-size: 13px; cursor: grab; user-select: none; background: #fff; }
.ds-item:active { cursor: grabbing; }
.ds-item.dragging { opacity: 0.45; border-color: #1677ff; }
.ds-idx { color: #999; width: 20px; text-align: right; }
.ds-pair { flex: 1; }
.ds-dir { color: #999; font-size: 12px; }
.ds-grip { color: #bbb; }
.ds-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px; }
</style>
