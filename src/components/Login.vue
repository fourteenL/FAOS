<script setup>
import { onMounted, reactive, ref } from "vue";
import { message } from "ant-design-vue";
import { useAppStore } from "../stores/app.js";

const emit = defineEmits(["logged-in"]);
const store = useAppStore();

const form = reactive({
  ctp_account: "",
  ctp_password: "",
  counter: "实盘",
  mqtt_host: "",
  broker: "9999",
  remember: false,
});
const remembered = ref(false);
const loading = ref(false);

onMounted(async () => {
  try {
    const st = await store.getLoginState();
    form.ctp_account = st.ctp_account || "";
    form.ctp_password = st.ctp_password || "";
    form.counter = st.counter || "实盘";
    form.mqtt_host = st.mqtt_host || "";
    form.broker = st.broker || "9999";
    form.remember = remembered.value = st.remembered;
  } catch (e) {
    /* 预填失败不阻塞登录 */
  }
});

async function doLogin(offline) {
  if (!form.ctp_account.trim()) {
    message.warning("请输入 CTP 账号");
    return;
  }
  if (!form.ctp_password && !remembered.value) {
    message.warning("请输入 CTP 密码");
    return;
  }
  loading.value = true;
  try {
    await store.login({
      ctp_account: form.ctp_account.trim(),
      ctp_password: form.ctp_password,
      counter: form.counter,
      mqtt_host: form.mqtt_host.trim(),
      broker: form.broker.trim(),
      remember: form.remember,
      offline,
    });
    emit("logged-in");
  } catch (e) {
    message.error(String(e));
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="login-wrap">
    <div class="login-card">
      <div class="title">FAOS 差价套利</div>
      <div class="sub">上期所跨期价差套利 · SimNow</div>
      <a-form layout="vertical" class="form">
        <a-form-item label="CTP 账号">
          <a-input v-model:value="form.ctp_account" placeholder="投资者代码，如 261518" size="large" @pressEnter="doLogin(false)" />
        </a-form-item>
        <a-form-item label="CTP 密码">
          <a-input-password v-model:value="form.ctp_password" placeholder="CTP 密码（同时用于 MQTT）" size="large" @pressEnter="doLogin(false)" />
        </a-form-item>
        <div class="row2">
          <a-form-item label="经纪商代码" class="half">
            <a-input v-model:value="form.broker" placeholder="如 9999" size="large" @pressEnter="doLogin(false)" />
          </a-form-item>
          <a-form-item label="柜台环境" class="half">
            <a-select v-model:value="form.counter" size="large" :options="[{ value: '实盘', label: '实盘（SimNow/生产证书）' }, { value: '测试', label: '测试（旧柜台，勿用）' }]" />
          </a-form-item>
        </div>
        <a-form-item label="行情服务器">
          <a-input v-model:value="form.mqtt_host" placeholder="MQTT 服务器地址" size="large" @pressEnter="doLogin(false)" />
        </a-form-item>
        <a-checkbox v-model:checked="form.remember">记住我（保存账号密码到本机）</a-checkbox>
        <div class="btns">
          <a-button type="primary" size="large" class="btn" :loading="loading && !form.offline" @click="doLogin(false)">登 录</a-button>
          <a-button size="large" class="btn" :loading="loading && form.offline" @click="doLogin(true)">离线登录（仅行情）</a-button>
        </div>
        <p class="hint">MQTT 账号自动使用 u + CTP 账号，密码同 CTP 密码。离线登录连接行情、不连接交易（非开盘时段使用）。</p>
      </a-form>
    </div>
  </div>
</template>

<style scoped>
.login-wrap { height: 100vh; display: flex; align-items: center; justify-content: center; background: #f0f5ff; }
.login-card { width: 420px; background: #fff; border-radius: 10px; box-shadow: 0 4px 24px rgba(0,0,0,.08); padding: 28px 32px; }
.title { font-size: 22px; font-weight: 700; text-align: center; }
.sub { color: #999; font-size: 12px; text-align: center; margin: 4px 0 18px; }
.row2 { display: flex; gap: 12px; }
.half { flex: 1; }
.btns { display: flex; gap: 12px; margin-top: 14px; }
.btn { flex: 1; }
.hint { color: #bbb; font-size: 12px; margin-top: 12px; margin-bottom: 0; }
</style>
