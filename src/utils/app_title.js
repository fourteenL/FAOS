// 窗口标题带版本号：启动时读取 Tauri 应用版本拼进标题（如「FAOS 差价套利 v0.1.0-beta.6」）。
// dev 与安装包各取其配置的真实版本；非 Tauri 环境（jsdom/纯浏览器）静默忽略。
// 注意：setTitle 需要 capabilities 授权 core:window:allow-set-title（窗口默认权限只读）。
export async function applyAppTitle(base = "FAOS 差价套利") {
  try {
    const [{ getVersion }, { getCurrentWindow }] = await Promise.all([
      import("@tauri-apps/api/app"),
      import("@tauri-apps/api/window"),
    ]);
    const ver = await getVersion();
    await getCurrentWindow().setTitle(`${base} v${ver}`);
  } catch (e) {
    // 真实环境失败（如权限缺失）打到控制台可排查；jsdom/浏览器环境无 Tauri API 属预期
    console.warn("[app_title] 标题版本号设置失败:", e?.message || e);
  }
}
