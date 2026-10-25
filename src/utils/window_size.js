// 窗口尺寸随登录态切换（2026-10-24 用户定稿）：
// 登录页=小窗（仅登录内容大小，不可调）；登录进入主界面=恢复 1400x900 可调。
// 非 Tauri 环境（jsdom/纯浏览器）静默忽略。需要 capabilities 授权：
// core:window:allow-set-size / allow-set-resizable / allow-center。

const LOGIN_SIZE = { width: 520, height: 600 };
const MAIN_SIZE = { width: 1400, height: 900 };

export async function setLoginWindowSize() {
  try {
    const { getCurrentWindow, LogicalSize } = await import("@tauri-apps/api/window");
    const w = getCurrentWindow();
    await w.setResizable(false);
    await w.setSize(new LogicalSize(LOGIN_SIZE.width, LOGIN_SIZE.height));
    await w.center();
  } catch (e) {
    console.warn("[window_size] 登录窗口尺寸设置失败:", e?.message || e);
  }
}

export async function setMainWindowSize() {
  try {
    const { getCurrentWindow, LogicalSize } = await import("@tauri-apps/api/window");
    const w = getCurrentWindow();
    await w.setResizable(true);
    await w.setSize(new LogicalSize(MAIN_SIZE.width, MAIN_SIZE.height));
    await w.center();
  } catch (e) {
    console.warn("[window_size] 主窗口尺寸设置失败:", e?.message || e);
  }
}
