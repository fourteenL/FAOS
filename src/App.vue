<script setup>
import { onMounted, ref } from "vue";
import { message, notification } from "ant-design-vue";
import { useAppStore } from "./stores/app.js";
import StatusBanner from "./components/StatusBanner.vue";
import MarketTable from "./components/MarketTable.vue";
import StrategyAdmin from "./components/StrategyAdmin.vue";
import SubscriptionDrawer from "./components/SubscriptionDrawer.vue";
import SplitLayout from "./components/SplitLayout.vue";
import OrderPanel from "./components/OrderPanel.vue";
import SettingsDrawer from "./components/SettingsDrawer.vue";
import Login from "./components/Login.vue";
import ProductFilter from "./components/ProductFilter.vue";

const store = useAppStore();
const settingsDrawer = ref(null);
const loggedIn = ref(false);
const subscriptionDrawer = ref(null);

function openSettings() {
  settingsDrawer.value?.open();
}

function openSubscription() {
  subscriptionDrawer.value?.open();
}

onMounted(async () => {
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
  store.$subscribe(() => {
    if (store.notices.length > seen) {
      for (const n of store.notices.slice(seen)) {
        (n.level === "error" ? notification.error : notification.warning)({
          message: n.level === "error" ? "错误" : "提示",
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
          <a-button size="small" @click="openSubscription">订阅管理</a-button>
          <a-button size="small" @click="openSettings">设置</a-button>
        </template>
      </StatusBanner>
      <SettingsDrawer ref="settingsDrawer" />
      <SubscriptionDrawer ref="subscriptionDrawer" />
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
