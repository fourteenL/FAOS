// 窗口标题带版本号：启动时读取 Tauri 应用版本拼进标题（如「FAOS 差价套利 v0.1.0-beta.6」）。
// dev 与安装包各取其配置的真实版本；非 Tauri 环境（jsdom/纯浏览器）静默忽略。
export async function applyAppTitle(base = "FAOS 差价套利") {
  try {
    const [{ getVersion }, { getCurrentWindow }] = await Promise.all([
      import("@tauri-apps/api/app"),
      import("@tauri-apps/api/window"),
    ]);
    const ver = await getVersion();
    await getCurrentWindow().setTitle(`${base} v${ver}`);
  } catch {
    /* 环境不支持时保持静态标题 */
  }
}
