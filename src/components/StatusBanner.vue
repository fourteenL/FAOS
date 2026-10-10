<script setup>
import { computed } from "vue";
import { useAppStore } from "../stores/app.js";

const store = useAppStore();

const DOT = { connected: "#52c41a", ready: "#52c41a", reconnecting: "#faad14", logining: "#faad14", disconnected: "#d9d9d9", error: "#ff4d4f" };
const ZH = { connected: "已连接", ready: "就绪", reconnecting: "重连中", logining: "登录中", disconnected: "未连接", error: "错误" };

const ctpColor = computed(() => DOT[store.ctpState] || "#d9d9d9");
const ZH_ALL = { ...ZH, connecting: "连接中…" };

async function connectCtp() {
  try {
    await store.connectCtp();
  } catch (e) {
    /* 状态经事件更新 */
  }
}
</script>

<template>
  <div class="banner">
    <slot name="actions" />
    <span class="item">
      <span class="dot" :style="{ background: ctpColor }" /> CTP(SimNow)：{{ ZH_ALL[store.ctpState] || store.ctpState }}
      <span v-if="store.ctpStep && store.ctpState !== 'ready'" class="step">{{ store.ctpStep }}…</span>
      <a v-if="!['ready', 'connected', 'logining'].includes(store.ctpState)" style="margin-left: 6px" @click="connectCtp">连接</a>
    </span>
  </div>
</template>

<style scoped>
.banner { display: flex; gap: 24px; align-items: center; padding: 2px 12px; background: #fafafa; border-bottom: 1px solid #f0f0f0; font-size: 13px; height: 32px; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
.step { color: #faad14; font-size: 12px; margin-left: 8px; }
</style>
