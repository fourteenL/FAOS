<script setup>
import { onMounted, ref } from "vue";
import { message, notification } from "ant-design-vue";
import { useAppStore } from "./stores/app.js";
import StatusBanner from "./components/StatusBanner.vue";
import MarketTable from "./components/MarketTable.vue";
import StrategyAdmin from "./components/StrategyAdmin.vue";
import SubscriptionDrawer from "./components/SubscriptionDrawer.vue";
import TriggerList from "./components/TriggerList.vue";
import AccountPositions from "./components/AccountPositions.vue";
import SplitLayout from "./components/SplitLayout.vue";
import OrderPanel from "./components/OrderPanel.vue";
import SettingsDrawer from "./components/SettingsDrawer.vue";
import Login from "./components/Login.vue";
import ProductFilter from "./components/ProductFilter.vue";
import { applyAppTitle } from "./utils/app_title.js";

const store = useAppStore();
const settingsDrawer = ref(null);
const loggedIn = ref(false);
const subscriptionDrawer = ref(null);
const triggerList = ref(null);

function openSettings() {
  settingsDrawer.value?.open();
}

function openSubscription() {
  subscriptionDrawer.value?.open();
}

function openTriggerList() {
  triggerList.value?.open();
}

async function doStopAll() {
  try {
    await store.stopAll();
  } catch (e) {
    /* 结果经通知推送 */
  }
}

onMounted(async () => {
  applyAppTitle(); // 窗口标题带版本号（如 FAOS 差价套利 v0.1.0-beta.6）
  try {
    await store.init(); // 状态/事件绑定先行（登录页也需要连接状态）
  } catch (e) {
    message.error("后端初始化失败: " + e);
  }
  // 初始化后统一弹 notice
  import("./stores/app.js").then(() => {});
});

function watchNotices() {
  let seen = 0;
  const NOTIFY = {
    error: notification.error,
    warn: notification.warning,
    success: notification.success,
    info: notification.info,
  };
  const TITLE = { error: "错误", warn: "提示", success: "成功", info: "提示" };
  store.$subscribe(() => {
    if (store.notices.length > seen) {
      for (const n of store.notices.slice(seen)) {
        (NOTIFY[n.level] || notification.info)({
          message: TITLE[n.level] || "提示",
          description: n.msg,
        });
      }
      seen = store.notices.length;
    }
  });
}
watchNotices();

</script>

<template>
  <Login v-if="!loggedIn" @logged-in="loggedIn = true" />
  <a-layout v-else style="height: 100vh">
    <a-layout-header style="height: auto; padding: 0; background: #fff; color: #333">
      <StatusBanner>
        <template #actions>
          <a-popconfirm title="停止全部策略？（撤所有触发单+停所有算法，持仓保留）" @confirm="doStopAll">
            <a-button size="small" danger>全部停止</a-button>
          </a-popconfirm>
          <a-button size="small" @click="openTriggerList">触发单 ({{ store.strategies.reduce((n, s) => n + (s.triggers?.length || 0), 0) }})</a-button>
          <a-button size="small" @click="openSubscription">订阅管理</a-button>
          <AccountPositions />
          <a-button size="small" @click="openSettings">设置</a-button>
        </template>
      </StatusBanner>
      <SettingsDrawer ref="settingsDrawer" />
      <SubscriptionDrawer ref="subscriptionDrawer" />
      <TriggerList ref="triggerList" />
    </a-layout-header>
    <a-layout-content style="height: calc(100vh - 40px)">
      <SplitLayout>
        <template #top>
          <div style="padding: 8px">
            <ProductFilter />
            <MarketTable />
          </div>
        </template>
        <template #bottom>
          <div class="left-pane">
            <div class="pane-title">策略管理</div>
            <StrategyAdmin />
          </div>
          <div class="right-pane">
            <template v-if="store.selected">
              <div class="pane-title">策略 #{{ store.selected.id }} {{ store.selected.pair }}</div>
              <OrderPanel />
            </template>
            <div v-else style="padding: 16px; color: #999">点击上方表格或左侧策略选择一个策略</div>
          </div>
        </template>
      </SplitLayout>
    </a-layout-content>
  </a-layout>
</template>

<style>
body { margin: 0; font-family: "Microsoft YaHei", sans-serif; }
.left-pane { flex: 0 0 46%; overflow: auto; border-right: 1px solid #f0f0f0; }
.right-pane { flex: 1 1 auto; overflow: auto; }
.pane-title { font-weight: 600; padding: 8px; background: #fafafa; border-bottom: 1px solid #f0f0f0; }
</style>
