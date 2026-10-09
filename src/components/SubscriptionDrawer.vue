<script setup>
import { reactive, ref } from "vue";
import { message } from "ant-design-vue";
import { useAppStore, STATUS_ZH, productOf } from "../stores/app.js";

const store = useAppStore();
const visible = ref(false);
const activeTab = ref("pair");
const form = reactive({ leg_a: "", leg_b: "", product: "", single_lots: 1, total_lots: 1 });
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

async function removeProduct() {
  const p = form.product.trim();
  if (!p) {
    message.warning("请先输入要退订的品种，如 ag");
    return;
  }
  const before = store.strategies.filter((s) => productOf(s) === p.toLowerCase()).length;
  try {
    await store.removeProduct(p);
    message.success(`已退订品种 ${p.toUpperCase()}（${before} 对已删除）`);
  } catch (e) {
    message.error(String(e));
  }
}

function canSubscribe(row) {
  return row.status === "unsubscribed" || row.status === "stopped";
}
</script>

<template>
  <div v-if="visible" class="fpanel" @click.self="visible = false">
    <div class="fpanel-card">
      <div class="fpanel-head">
        <span>订阅管理</span>
        <a class="close" @click="visible = false">×</a>
      </div>
      <a-tabs v-model:active-key="activeTab" size="small">
        <a-tab-pane key="pair" tab="自行创建合约对">
          <a-form layout="inline">
            <a-form-item label="A腿"><a-input v-model:value="form.leg_a" placeholder="如 ag2702" style="width: 100px" /></a-form-item>
            <a-form-item label="B腿"><a-input v-model:value="form.leg_b" placeholder="如 ag2612" style="width: 100px" /></a-form-item>
            <a-form-item label="单份"><a-input-number v-model:value="form.single_lots" :min="1" style="width: 64px" /></a-form-item>
            <a-form-item label="总份"><a-input-number v-model:value="form.total_lots" :min="1" style="width: 64px" /></a-form-item>
            <a-form-item><a-button type="primary" :loading="submitting" @click="submit">订阅</a-button></a-form-item>
          </a-form>
        </a-tab-pane>
        <a-tab-pane key="product" tab="品种自动匹配">
          <a-form layout="inline">
            <a-form-item label="品种"><a-input v-model:value="form.product" placeholder="如 ag" style="width: 100px" /></a-form-item>
            <a-form-item label="单份"><a-input-number v-model:value="form.single_lots" :min="1" style="width: 64px" /></a-form-item>
            <a-form-item label="总份"><a-input-number v-model:value="form.total_lots" :min="1" style="width: 64px" /></a-form-item>
            <a-form-item>
              <span class="hint">展开该品种全部在市合约组合</span>
              <a-button type="primary" :loading="submitting" @click="submit" style="margin-left: 8px">订阅</a-button>
            </a-form-item>
          </a-form>
          <a-form layout="inline" style="margin-top: 6px">
            <a-form-item>
              <span class="hint">退订将删除该品种全部组合</span>
              <a-popconfirm :title="`退订品种 ${form.product.trim().toUpperCase() || '(未输入)'} 的全部合约？`" @confirm="removeProduct">
                <a-button danger size="small">退订该品种</a-button>
              </a-popconfirm>
            </a-form-item>
          </a-form>
        </a-tab-pane>
      </a-tabs>

      <a-divider orientation="left" plain class="div">当前订阅</a-divider>
      <a-table
        :columns="[
          { title: '编号', dataIndex: 'id', key: 'id', width: 56, align: 'center' },
          { title: '合约对', dataIndex: 'pair', key: 'pair', align: 'center' },
          { title: '状态', key: 'status', width: 84, align: 'center' },
          { title: '操作', key: 'action', width: 120, align: 'center' },
        ]"
        :data-source="store.strategies"
        :pagination="{ pageSize: 5, size: 'small' }"
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
    </div>
  </div>
</template>

<style scoped>
.fpanel { position: fixed; inset: 0; z-index: 1000; background: rgba(0,0,0,.45);
  display: flex; align-items: flex-start; justify-content: center; padding-top: 56px; }
.fpanel-card { width: 440px; max-height: 70vh; overflow-y: auto;
  background: #fff; border-radius: 8px; box-shadow: 0 6px 24px rgba(0,0,0,.18); padding: 12px 14px; }
.fpanel-head { display: flex; justify-content: space-between; font-weight: 600;
  font-size: 14px; margin-bottom: 8px; }
.close { font-size: 18px; color: #999; line-height: 1; }
.hint { color: #999; font-size: 12px; }
.div { margin: 12px 0 6px; font-size: 12px; }
</style>
