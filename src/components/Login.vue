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
  mqtt_port: null,
  ctp_server: "simnow1",
  ctp_td: "",
  ctp_md: "",
  broker: "9999",
  remember: false,
});

// CTP 前置预设（SimNow 2025-06 起新地址）；custom = 手填交易/行情前置
const CTP_SERVERS = [
  { value: "simnow1", label: "SimNow 第一组（电信）", td: "tcp://182.254.243.31:30001", md: "tcp://182.254.243.31:30011" },
  { value: "simnow2", label: "SimNow 第二组（电信）", td: "tcp://182.254.243.31:30002", md: "tcp://182.254.243.31:30012" },
  { value: "simnow3", label: "SimNow 第三组（电信）", td: "tcp://182.254.243.31:30003", md: "tcp://182.254.243.31:30013" },
  { value: "simnow724", label: "SimNow 7×24（收盘后/周末）", td: "tcp://182.254.243.31:40001", md: "tcp://182.254.243.31:40011" },
  { value: "custom", label: "自定义前置地址", td: "", md: "" },
];

function matchServer(td, md) {
  const hit = CTP_SERVERS.find((x) => x.value !== "custom" && x.td === td && x.md === md);
  return hit ? hit.value : "custom";
}

function resolveServer() {
  const p = CTP_SERVERS.find((x) => x.value === form.ctp_server);
  if (!p || p.value === "custom") return { td: form.ctp_td.trim(), md: form.ctp_md.trim() };
  return { td: p.td, md: p.md };
}

const remembered = ref(false);
const loading = ref(false);

onMounted(async () => {
  try {
    const st = await store.getLoginState();
    form.ctp_account = st.ctp_account || "";
    form.ctp_password = st.ctp_password || "";
    form.counter = st.counter || "实盘";
    form.mqtt_host = st.mqtt_host || "";
    form.mqtt_port = st.mqtt_port || null;
    form.ctp_server = st.ctp_td ? matchServer(st.ctp_td, st.ctp_md) : "simnow1";
    form.ctp_td = st.ctp_td || "";
    form.ctp_md = st.ctp_md || "";
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
    const srv = resolveServer();
    await store.login({
      ctp_account: form.ctp_account.trim(),
      ctp_password: form.ctp_password,
      counter: form.counter,
      mqtt_host: form.mqtt_host.trim(),
      mqtt_port: form.mqtt_port ? Number(form.mqtt_port) : undefined,
      ctp_td: srv.td,
      ctp_md: srv.md,
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
        <a-form-item label="CTP 服务器">
          <a-select v-model:value="form.ctp_server" size="large" :options="CTP_SERVERS" />
        </a-form-item>
        <div v-if="form.ctp_server === 'custom'" class="row2">
          <a-form-item label="交易前置" class="half">
            <a-input v-model:value="form.ctp_td" placeholder="tcp://host:port" size="large" @pressEnter="doLogin(false)" />
          </a-form-item>
          <a-form-item label="行情前置" class="half">
            <a-input v-model:value="form.ctp_md" placeholder="tcp://host:port" size="large" @pressEnter="doLogin(false)" />
          </a-form-item>
        </div>
        <div class="row2">
          <a-form-item label="行情服务器" class="half">
            <a-input v-model:value="form.mqtt_host" placeholder="MQTT 地址" size="large" @pressEnter="doLogin(false)" />
          </a-form-item>
          <a-form-item label="端口" class="half">
            <a-input-number v-model:value="form.mqtt_port" :min="1" :max="65535" placeholder="1889" style="width: 100%" size="large" @pressEnter="doLogin(false)" />
          </a-form-item>
        </div>
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
