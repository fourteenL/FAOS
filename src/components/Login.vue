<script setup>
import { computed, onMounted, reactive, ref } from "vue";
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

// 环境管理弹窗（2026-10-24 改版）：打开即见列表，编辑/添加共用表单，保存按钮固定底部
const envMgrOpen = ref(false);
const envForm = reactive({ name: "", mode: "实盘", td: "", md: "" });
const editingName = ref("");  // 非空=正在编辑该原名的环境（名称可改，保存时先删旧再存新）

async function openEnvMgr() {
  envMgrOpen.value = true;
  resetEnvForm();
}

function resetEnvForm() {
  editingName.value = "";
  envForm.name = "";
  envForm.mode = "实盘";
  envForm.td = "";
  envForm.md = "";
}

function editEnv(e) {
  editingName.value = e.name;
  envForm.name = e.name;
  envForm.mode = e.mode;
  envForm.td = e.td;
  envForm.md = e.md;
}

async function saveEnv() {
  const name = envForm.name.trim();
  if (!name) {
    message.warning("环境名称不能为空");
    return;
  }
  try {
    if (editingName.value && editingName.value !== name) {
      await call("remove_environment", { name: editingName.value });  // 改名：先删旧
    }
    const r = await call("add_environment", { ...envForm, name });
    environments.value = r.environments;
    form.counter = name;
    message.success("环境已保存");
    resetEnvForm();
  } catch (e) {
    message.error(String(e));
  }
}

async function removeEnv(name) {
  try {
    const r = await call("remove_environment", { name });
    environments.value = r.environments;
    if (form.counter === name) form.counter = environments.value[0]?.name || "";
    if (editingName.value === name) resetEnvForm();
    message.success("环境已删除");
  } catch (e) {
    message.error(String(e));
  }
}

const remembered = ref(false);
const loading = ref(false);

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
          {{ currentEnv.mode }} · 交易 {{ currentEnv.td }} · 行情 {{ currentEnv.md }}
        </div>
        <a-checkbox v-model:checked="form.remember">记住我（保存账号密码到本机）</a-checkbox>
        <div class="btns">
          <a-button type="primary" size="large" class="btn" :loading="loading" @click="doLogin()">登 录</a-button>
        </div>
        <p class="hint">行情由本地 CTP 两腿合成，登录即连接交易与行情（SimNow 交易时段 8:30–16:30 / 夜盘）。</p>
      </a-form>

      <!-- 柜台环境管理：列表优先，编辑/添加共用表单，保存按钮固定底部 -->
      <div v-if="envMgrOpen" class="env-mgr" @click.self="envMgrOpen = false">
        <div class="env-card">
          <div class="env-head">
            <span>柜台环境管理</span>
            <a class="close" @click="envMgrOpen = false">×</a>
          </div>
          <div class="env-body">
            <div class="env-list">
              <div v-for="e in environments" :key="e.name" class="env-row"
                   :class="{ editing: editingName === e.name }">
                <div class="env-info-cell">
                  <span class="env-name mono">{{ e.name }}</span>
                  <span class="env-detail">{{ e.mode }} · {{ e.td }}</span>
                </div>
                <span class="env-ops">
                  <a @click="editEnv(e)">编辑</a>
                  <a-popconfirm :title="`删除环境 ${e.name}？`" @confirm="removeEnv(e.name)">
                    <a style="color: #cf1322">删除</a>
                  </a-popconfirm>
                </span>
              </div>
            </div>
            <div class="env-form-title">{{ editingName ? `编辑：${editingName}` : "添加环境" }}</div>
            <a-form layout="vertical" class="env-form">
              <a-form-item label="环境名称"><a-input v-model:value="envForm.name" placeholder="如 华泰实盘 / SimNow 第一组" /></a-form-item>
              <a-form-item label="柜台模式">
                <a-select v-model:value="envForm.mode" :options="[{ value: '实盘', label: '实盘（生产证书）' }, { value: '测试', label: '测试' }]" />
              </a-form-item>
              <a-form-item label="交易前置"><a-input v-model:value="envForm.td" placeholder="tcp://host:port" /></a-form-item>
              <a-form-item label="行情前置"><a-input v-model:value="envForm.md" placeholder="tcp://host:port" /></a-form-item>
            </a-form>
          </div>
          <div class="env-foot">
            <a-button v-if="editingName" size="small" @click="resetEnvForm">取消编辑</a-button>
            <a-button type="primary" size="small" @click="saveEnv">
              {{ editingName ? "保存修改" : "保存环境" }}
            </a-button>
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
.env-info { color: #999; font-size: 12px; margin: -8px 0 10px; }
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
.env-list { margin: 2px 0 12px; }
.env-row { display: flex; justify-content: space-between; align-items: center; font-size: 12px;
  padding: 6px 4px; border-bottom: 1px solid #f5f5f5; border-radius: 4px; }
.env-row.editing { background: #e6f4ff; }  /* 正在编辑的行高亮 */
.env-info-cell { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.env-name { font-weight: 600; }
.env-detail { color: #999; font-size: 11px; }
.env-ops { display: flex; gap: 10px; flex-shrink: 0; }
.env-form-title { font-size: 12px; color: #666; margin: 4px 0 2px; font-weight: 600; }
.mono { font-family: monospace; }
</style>
