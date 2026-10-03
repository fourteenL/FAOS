<script setup>
import { reactive, ref } from "vue";
import { message } from "ant-design-vue";
import { call } from "../api/pytauri.js";

const visible = ref(false);
const loading = ref(false);
const form = reactive({
  mqtt: { host: "", port: 1883, user: "", password: "" },
  ctp: {},
  algo: { payup: 5, interval: 3, lock: false, timeout: 300 },
});

async function open() {
  visible.value = true;
  loading.value = true;
  try {
    const s = await call("get_settings", {});
    form.mqtt = { ...s.mqtt };
    form.ctp = { ...s.ctp };
    form.algo = { ...s.algo };
  } catch (e) {
    message.error("读取设置失败: " + e);
  } finally {
    loading.value = false;
  }
}
defineExpose({ open });

async function save() {
  try {
    await call("save_settings", {
      mqtt: { ...form.mqtt },
      ctp: { ...form.ctp },
      algo: { ...form.algo },
    });
    message.success("已保存（连接参数重启应用后生效；密码未改动时无需重填）");
    visible.value = false;
  } catch (e) {
    message.error(String(e));
  }
}
</script>

<template>
  <a-modal v-model:open="visible" title="连接与算法设置" width="560" :get-container="false" :mask-closable="false">
    <a-spin :spinning="loading">
      <a-form layout="vertical">
        <a-divider orientation="left" plain>MQTT 行情</a-divider>
        <div class="grid2">
          <a-form-item label="地址"><a-input v-model:value="form.mqtt.host" /></a-form-item>
          <a-form-item label="端口"><a-input-number v-model:value="form.mqtt.port" style="width: 100%" /></a-form-item>
          <a-form-item label="用户名"><a-input v-model:value="form.mqtt.user" /></a-form-item>
          <a-form-item label="密码"><a-input-password v-model:value="form.mqtt.password" /></a-form-item>
        </div>

        <a-divider orientation="left" plain>CTP 交易（SimNow）</a-divider>
        <div class="grid2">
          <a-form-item label="用户名"><a-input v-model:value="form.ctp['用户名']" /></a-form-item>
          <a-form-item label="密码"><a-input-password v-model:value="form.ctp['密码']" /></a-form-item>
          <a-form-item label="经纪商代码"><a-input v-model:value="form.ctp['经纪商代码']" /></a-form-item>
          <a-form-item label="柜台环境">
            <a-select v-model:value="form.ctp['柜台环境']" :options="[{ value: '测试', label: '测试（SimNow）' }, { value: '实盘', label: '实盘' }]" />
          </a-form-item>
          <a-form-item label="交易服务器" class="span2"><a-input v-model:value="form.ctp['交易服务器']" /></a-form-item>
          <a-form-item label="行情服务器" class="span2"><a-input v-model:value="form.ctp['行情服务器']" /></a-form-item>
          <a-form-item label="产品名称"><a-input v-model:value="form.ctp['产品名称']" /></a-form-item>
          <a-form-item label="授权编码"><a-input v-model:value="form.ctp['授权编码']" /></a-form-item>
        </div>

        <a-divider orientation="left" plain>价差算法</a-divider>
        <div class="grid2">
          <a-form-item label="追价跳数上限 (payup)"><a-input-number v-model:value="form.algo.payup" :min="0" style="width: 100%" /></a-form-item>
          <a-form-item label="轮询间隔（秒）"><a-input-number v-model:value="form.algo.interval" :min="1" style="width: 100%" /></a-form-item>
          <a-form-item label="超时停止（秒）"><a-input-number v-model:value="form.algo.timeout" :min="10" style="width: 100%" /></a-form-item>
          <a-form-item label="锁仓"><a-switch v-model:checked="form.algo.lock" /></a-form-item>
        </div>
      </a-form>
    </a-spin>
    <template #footer>
      <a-space>
        <a-button @click="visible = false">取消</a-button>
        <a-button type="primary" @click="save">保存</a-button>
      </a-space>
    </template>
  </a-modal>
</template>

<style scoped>
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0 12px; }
.span2 { grid-column: span 2; }
</style>
