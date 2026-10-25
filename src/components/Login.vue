<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import { message } from "ant-design-vue";
import { call } from "../api/pytauri.js";
import { useAppStore } from "../stores/app.js";

const emit = defineEmits(["logged-in"]);
const store = useAppStore();

const form = reactive({
  ctp_account: "",
  ctp_password: "",
  counter: "",       // 环境名（environments 里选）
  broker: "9999",
  remember: false,
});

// 柜台环境列表（后端下发，可增删）；选中即以环境名登录（后端解析前置/模式）
const environments = ref([]);
const envOptions = computed(() => environments.value.map((e) => ({ value: e.name, label: e.name })));
const currentEnv = computed(() => environments.value.find((e) => e.name === form.counter));

// 环境管理弹窗（2026-10-25 定稿）：行内直接编辑——无边框输入框就地改，
// 行尾「-」删除、列表尾「+」添加，底部「保存」统一提交
const envMgrOpen = ref(false);
const envRows = ref([]);  // [{orig: 原名|null新增, name, mode, td, md}]

async function openEnvMgr() {
  envMgrOpen.value = true;
  envRows.value = environments.value.map((e) => ({ ...e, orig: e.name }));
  checkAllEnv();  // 打开即检测全部环境延迟
}

function addEnvRow() {
  envRows.value.push({ orig: null, name: "", mode: "实盘", td: "", md: "" });
}

async function removeEnvRow(idx) {
  const row = envRows.value[idx];
  try {
    if (row.orig) {
      await call("remove_environment", { name: row.orig });
    }
    envRows.value.splice(idx, 1);
    const st = await call("get_login_state");  // 拉最新列表
    environments.value = st.environments || environments.value;
    if (form.counter === row.orig) form.counter = environments.value[0]?.name || "";
    message.success("环境已删除");
  } catch (e) {
    message.error(String(e));
  }
}

async function saveEnvRows() {
  const rows = envRows.value.filter((r) => r.name.trim());
  if (envRows.value.some((r) => !r.name.trim() && (r.td || r.md))) {
    message.warning("有环境未填名称，该行已跳过");
  }
  if (!rows.length) {
    message.warning("至少保留一个柜台环境");
    return;
  }
  try {
    for (const r of rows) {
      if (r.orig && r.orig !== r.name.trim()) {
        await call("remove_environment", { name: r.orig });  // 改名：先删旧
      }
      const r2 = await call("add_environment", { name: r.name.trim(), mode: r.mode, td: r.td.trim(), md: r.md.trim() });
      environments.value = r2.environments;
    }
    form.counter = rows[rows.length - 1].name.trim();
    message.success("环境已保存");
    envMgrOpen.value = false;
  } catch (e) {
    message.error(String(e));
  }
}

const remembered = ref(false);
const loading = ref(false);

// 环境延迟（2026-10-25）：name -> {td, md}（毫秒；-1=不通；null=检测中）
const envLatency = reactive({});
let latencySeq = 0;  // 弹窗重开/手动重测时作废旧请求

async function checkEnv(e) {
  envLatency[e.name] = null;  // 检测中
  try {
    const r = await call("check_env_latency", { td: e.td, md: e.md });
    envLatency[e.name] = r;
  } catch (err) {
    envLatency[e.name] = { td: -1, md: -1 };
  }
}

async function checkAllEnv() {
  const seq = ++latencySeq;
  for (const e of environments.value) {
    if (seq !== latencySeq) return;  // 已作废
    checkEnv(e);                      // 逐个发起（后端各自并行 td/md）
  }
}

function latencyTdText(e) {
  const l = envLatency[e.name];
  if (!l) return "检测中…";
  return l.td < 0 ? "不通" : `${l.td}ms`;
}

function latencyMdText(e) {
  const l = envLatency[e.name];
  if (!l) return "检测中…";
  return l.md < 0 ? "不通" : `${l.md}ms`;
}

function latencyText(e) {
  const l = envLatency[e.name];
  if (!l) return "检测中…";
  const fmt = (v) => (v < 0 ? "不通" : `${v}ms`);
  return `交易 ${fmt(l.td)} · 行情 ${fmt(l.md)}`;
}

function latencyCls(e) {
  const l = envLatency[e.name];
  if (!l) return "lat-checking";
  const worst = Math.max(l.td, l.md);
  if (worst < 0) return "lat-bad";
  if (worst < 150) return "lat-good";
  if (worst < 400) return "lat-mid";
  return "lat-bad";
}

onMounted(async () => {
  try {
    const st = await store.getLoginState();
    form.ctp_account = st.ctp_account || "";
    form.ctp_password = st.ctp_password || "";
    environments.value = st.environments || [];
    form.counter = st.counter || environments.value[0]?.name || "";
    form.broker = st.broker || "9999";
    form.remember = remembered.value = st.remembered;
  } catch (e) {
    /* 预填失败不阻塞登录 */
  }
});

watch(() => form.counter, () => {
  if (currentEnv.value) checkEnv(currentEnv.value);  // 切换环境即测当前延迟
});

async function doLogin() {
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
      counter: form.counter, // 环境名，后端解析模式与前置
      broker: form.broker.trim(),
      remember: form.remember,
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
      <div class="brand">
        <span class="brand-bar"></span>
        <div class="title">FAOS 差价套利</div>
        <span class="brand-bar"></span>
      </div>
      <div class="sub">上期所跨期价差套利 · SimNow</div>
      <div class="sub-line"></div>
      <a-form layout="vertical" class="form">
        <a-form-item label="CTP 账号">
          <a-input v-model:value="form.ctp_account" placeholder="投资者代码，如 261518" size="large" @pressEnter="doLogin()" />
        </a-form-item>
        <a-form-item label="CTP 密码">
          <a-input-password v-model:value="form.ctp_password" placeholder="CTP 密码" size="large" @pressEnter="doLogin()" />
        </a-form-item>
        <a-form-item label="柜台环境">
          <div style="display: flex; gap: 8px">
            <a-select v-model:value="form.counter" size="large" style="flex: 1"
              :options="envOptions" placeholder="选择柜台环境" />
            <a-button size="large" @click="openEnvMgr">管理</a-button>
          </div>
        </a-form-item>
        <div v-if="currentEnv" class="env-info">
          <div>交易 {{ currentEnv.td }} <span :class="latencyCls(currentEnv)">{{ latencyTdText(currentEnv) }}</span></div>
          <div>行情 {{ currentEnv.md }} <span :class="latencyCls(currentEnv)">{{ latencyMdText(currentEnv) }}</span></div>
        </div>
        <a-checkbox v-model:checked="form.remember">记住我（保存账号密码到本机）</a-checkbox>
        <div class="btns">
          <a-button type="primary" size="large" class="btn" :loading="loading" @click="doLogin()">登 录</a-button>
        </div>
      </a-form>

      <!-- 柜台环境管理：列表优先，编辑/添加共用表单，保存按钮固定底部 -->
      <div v-if="envMgrOpen" class="env-mgr" @click.self="envMgrOpen = false">
        <div class="env-card">
          <div class="env-head">
            <span>柜台环境管理</span>
            <span>
              <a class="check-all" @click="checkAllEnv">检测全部</a>
              <a class="close" @click="envMgrOpen = false">×</a>
            </span>
          </div>
          <div class="env-body">
            <div v-for="(r, i) in envRows" :key="r.orig || `new-${i}`" class="env-row">
              <div class="env-line1">
                <a-input v-model:value="r.name" :bordered="false" size="small"
                  placeholder="环境名称" class="cell-name" />
                <a-select v-model:value="r.mode" :bordered="false" size="small"
                  :options="[{ value: '实盘', label: '实盘' }, { value: '测试', label: '测试' }]"
                  class="cell-mode" />
                <a-popconfirm :title="`删除环境 ${r.name || '（未命名）'}？`" @confirm="removeEnvRow(i)">
                  <a-button size="small" shape="circle" class="op-btn del" title="删除该环境">−</a-button>
                </a-popconfirm>
              </div>
              <div class="env-line2">
                <a-input v-model:value="r.td" :bordered="false" size="small"
                  placeholder="交易前置 tcp://host:port" class="cell-url" />
                <a-input v-model:value="r.md" :bordered="false" size="small"
                  placeholder="行情前置 tcp://host:port" class="cell-url" />
              </div>
              <div v-if="r.name.trim()" class="env-lat" :class="latencyCls(r)">
                延迟 {{ latencyText(r) }}
                <a class="recheck" @click="checkEnv(r)">重测</a>
              </div>
            </div>
            <div class="env-add">
              <a-button size="small" shape="circle" class="op-btn" title="添加环境" @click="addEnvRow">+</a-button>
              <span class="env-add-hint">添加环境</span>
            </div>
          </div>
          <div class="env-foot">
            <a-button type="primary" size="small" @click="saveEnvRows">保存</a-button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 沉浸式登录（2026-10-24）：窗口即登录页——纯白底、无卡片边界/阴影，
   与主界面白底 AntD 风格同源，消除小窗里的色块割裂感 */
.login-wrap { height: 100vh; background: #fff; display: flex; align-items: center; justify-content: center; }
.login-card { width: 100%; max-width: 340px; padding: 0 8px; }
.brand { display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 4px; }
.brand-bar { width: 4px; height: 22px; border-radius: 2px; background: #1677ff; }
.title { font-size: 21px; font-weight: 700; text-align: center; }
.sub { color: #999; font-size: 12px; text-align: center; margin: 4px 0 6px; }
.sub-line { width: 48px; height: 2px; margin: 0 auto 24px; background: #1677ff; border-radius: 1px; opacity: .85; }
.row2 { display: flex; gap: 12px; }
.half { flex: 1; }
.btns { display: flex; gap: 12px; margin-top: 14px; }
.btn { flex: 1; }
.hint { color: #bbb; font-size: 12px; margin-top: 12px; margin-bottom: 0; }
.env-info { color: #999; font-size: 12px; margin: -8px 0 10px; line-height: 1.7; }
.env-mgr { position: fixed; inset: 0; z-index: 1001; background: rgba(0,0,0,.45);
  display: flex; align-items: flex-start; justify-content: center; padding-top: 56px; }
.env-card { width: 440px; max-height: 70vh; overflow: hidden; background: #fff;
  border-radius: 8px; box-shadow: 0 6px 24px rgba(0,0,0,.18); padding: 12px 14px;
  display: flex; flex-direction: column; }
.env-head { display: flex; justify-content: space-between; font-weight: 600; font-size: 14px;
  margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px solid #f0f0f0;
  flex-shrink: 0; }  /* 标题/关闭固定顶部（2026-10-24） */
.env-body { overflow-y: auto; flex: 1 1 auto; min-height: 0; }  /* 内容独立滚动 */
.env-foot { display: flex; gap: 8px; justify-content: flex-end; flex-shrink: 0;
  padding-top: 10px; border-top: 1px solid #f0f0f0; }  /* 保存按钮固定底部 */
.close { font-size: 18px; color: #999; line-height: 1; cursor: pointer; }
.env-row { padding: 2px 4px 4px; border-bottom: 1px solid #f5f5f5; }
.env-line1 { display: flex; align-items: center; gap: 2px; }
.cell-name { flex: 1 1 auto; font-weight: 600; }
.cell-mode { flex: 0 0 78px; }
.cell-url { flex: 1 1 0; min-width: 0; font-size: 12px; color: #555; }
.env-line2 { display: flex; gap: 8px; }
.op-btn { width: 22px; height: 22px; min-width: 22px; display: inline-flex;
  align-items: center; justify-content: center; font-size: 14px; line-height: 1;
  color: #1677ff; border-color: #1677ff; }
.op-btn.del { color: #cf1322; border-color: #cf1322; }
.env-lat { font-size: 11px; padding: 1px 4px 2px; display: flex; gap: 8px; align-items: center; }
.lat-good { color: #389e0d; }
.lat-mid { color: #d46b08; }
.lat-bad { color: #cf1322; }
.lat-checking { color: #999; }
.check-all { font-size: 12px; color: #1677ff; margin-right: 12px; }
.recheck { color: #1677ff; font-size: 11px; }
.env-add { display: flex; align-items: center; gap: 8px; padding: 8px 4px 2px; }
.env-add-hint { color: #999; font-size: 12px; }
:deep(.env-row .ant-input-borderless), :deep(.env-row .ant-select-borderless .ant-select-selector) {
  padding: 2px 4px; }  /* 无边框输入框紧凑化 */
:deep(.cell-mode .ant-select-selection-item) { font-size: 12px; }
.mono { font-family: monospace; }
</style>
