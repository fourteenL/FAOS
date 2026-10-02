<script setup>
import { reactive, ref } from "vue";
import { message } from "ant-design-vue";
import { useAppStore, STATUS_ZH, productOf } from "../stores/app.js";

const store = useAppStore();
const visible = ref(false);
const activeTab = ref("pair");
const form = reactive({ leg_a: "", leg_b: "", product: "", single_lots: 1, total_lots: 10 });
const submitting = ref(false);

async function open() {
  visible.value = true;
}

defineExpose({ open });

async function submit() {
  submitting.value = true;
  try {
    if (activeTab.value === "pair") {
      await store.createStrategy({
        mode: "pair", leg_a: form.leg_a.trim(), leg_b: form.leg_b.trim(),
        single_lots: form.single_lots, total_lots: form.total_lots,
      });
    } else {
      await store.createStrategy({
        mode: "product", product: form.product.trim(),
        single_lots: form.single_lots, total_lots: form.total_lots,
      });
    }
    if (activeTab.value === "product") {
      const p = form.product.trim().toUpperCase();
      const count = store.strategies.filter((s) => productOf(s) === p.toLowerCase()).length;
      if (count > 0) {
        message.success(`品种 ${p} 已订阅 ${count} 对（表格已显示，单份/总份已按本次输入更新）`);
      } else {
        message.info(
          `已订阅品种「${p}」，等待行情服务器展开回执后自动生成策略行` +
          (store.mqttState === "connected" ? "" : "（MQTT 未连接，将在连接后生效）")
        );
      }
    } else {
      message.success("已订阅，合约对已加入行情列表");
    }
  } catch (e) {
    message.error(String(e));
  } finally {
    submitting.value = false;
  }
}

async function resub(id) {
  try {
    await store.subscribe(id);
  } catch (e) {
    message.error(String(e));
  }
}

async function remove(id) {
  try {
    await store.removeStrategy(id);
  } catch (e) {
    message.error(String(e));
  }
}

function canSubscribe(row) {
  return row.status === "unsubscribed" || row.status === "stopped";
}
</script>

<template>
  <a-drawer v-model:open="visible" title="订阅管理" width="560" :get-container="false">
    <a-tabs v-model:active-key="activeTab" size="small">
      <a-tab-pane key="pair" tab="自行创建合约对">
        <a-form layout="inline">
          <a-form-item label="A腿"><a-input v-model:value="form.leg_a" placeholder="如 ag2702" style="width: 110px" /></a-form-item>
          <a-form-item label="B腿"><a-input v-model:value="form.leg_b" placeholder="如 ag2612" style="width: 110px" /></a-form-item>
          <a-form-item label="单份"><a-input-number v-model:value="form.single_lots" :min="1" style="width: 70px" /></a-form-item>
          <a-form-item label="总份"><a-input-number v-model:value="form.total_lots" :min="1" style="width: 70px" /></a-form-item>
          <a-form-item><a-button type="primary" :loading="submitting" @click="submit">订阅</a-button></a-form-item>
        </a-form>
      </a-tab-pane>
      <a-tab-pane key="product" tab="品种自动匹配">
        <a-form layout="inline">
          <a-form-item label="品种"><a-input v-model:value="form.product" placeholder="如 ag" style="width: 110px" /></a-form-item>
          <a-form-item label="单份"><a-input-number v-model:value="form.single_lots" :min="1" style="width: 70px" /></a-form-item>
          <a-form-item label="总份"><a-input-number v-model:value="form.total_lots" :min="1" style="width: 70px" /></a-form-item>
          <a-form-item>
            <span class="hint">展开该品种全部在市合约组合</span>
            <a-button type="primary" :loading="submitting" @click="submit" style="margin-left: 8px">订阅</a-button>
          </a-form-item>
        </a-form>
      </a-tab-pane>
    </a-tabs>

    <a-divider orientation="left" plain class="div">当前订阅</a-divider>
    <a-table
      :columns="[
        { title: '编号', dataIndex: 'id', key: 'id', width: 60, align: 'center' },
        { title: '合约对', dataIndex: 'pair', key: 'pair', align: 'center' },
        { title: '状态', key: 'status', width: 90, align: 'center' },
        { title: '操作', key: 'action', width: 130, align: 'center' },
      ]"
      :data-source="store.strategies"
      :pagination="false"
      size="small"
      row-key="id"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'status'">{{ STATUS_ZH[record.status] || record.status }}</template>
        <template v-else-if="column.key === 'action'">
          <a-space>
            <a v-if="canSubscribe(record)" @click="resub(record.id)">重新订阅</a>
            <a-popconfirm title="删除该策略并退订行情？" @confirm="remove(record.id)">
              <a style="color: #cf1322">删除</a>
            </a-popconfirm>
          </a-space>
        </template>
      </template>
    </a-table>
  </a-drawer>
</template>

<style scoped>
.hint { color: #999; font-size: 12px; }
.div { margin: 12px 0 6px; font-size: 12px; }
</style>
