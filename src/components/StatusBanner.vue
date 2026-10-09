<script setup>
import { computed } from "vue";
import { useAppStore } from "../stores/app.js";

const store = useAppStore();

const DOT = { connected: "#52c41a", ready: "#52c41a", reconnecting: "#faad14", logining: "#faad14", disconnected: "#d9d9d9", error: "#ff4d4f" };
const ZH = { connected: "已连接", ready: "就绪", reconnecting: "重连中", logining: "登录中", disconnected: "未连接", error: "错误" };

const mqttColor = computed(() => DOT[store.mqttState] || "#d9d9d9");
const ctpColor = computed(() => DOT[store.ctpState] || "#d9d9d9");
const ZH_ALL = { ...ZH, connecting: "连接中…" };

async function connectMqtt() {
  try {
    await store.connectMqtt();
  } catch (e) {
    /* 状态经事件更新 */
  }
}
async function connectCtp() {
  try {
    await store.connectCtp();
  } catch (e) {
    /* 状态经事件更新 */
  }
}
const offline = computed(() => store.mqttState !== "connected" || (store.ctpState !== "ready" && store.ctpState !== "disconnected"));
</script>

<template>
  <div class="banner">
    <slot name="actions" />
    <span class="item">
      <span class="dot" :style="{ background: mqttColor }" /> 行情（7×24）：{{ ZH_ALL[store.mqttState] || store.mqttState }}
      <a v-if="!['connected', 'connecting'].includes(store.mqttState)" style="margin-left: 6px" @click="connectMqtt">连接</a>
      <a v-if="store.mqttState === 'connected'" style="margin-left: 6px; color: #999" @click="store.disconnectMqtt()">断开</a>
    </span>
    <span class="item">
      <span class="dot" :style="{ background: ctpColor }" /> CTP(SimNow)：{{ ZH_ALL[store.ctpState] || store.ctpState }}
      <a v-if="!['ready', 'connected', 'logining'].includes(store.ctpState)" style="margin-left: 6px" @click="connectCtp">连接</a>
    </span>
  </div>
</template>

<style scoped>
.banner { display: flex; gap: 24px; align-items: center; padding: 2px 12px; background: #fafafa; border-bottom: 1px solid #f0f0f0; font-size: 13px; height: 32px; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
.hint { color: #999; font-size: 12px; margin-left: 4px; }
</style>
