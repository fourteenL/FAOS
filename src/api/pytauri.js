import { pyInvoke } from "tauri-plugin-pytauri-api";
import { listen } from "@tauri-apps/api/event";

/** pyInvoke 包装：RootModel 包装与数组响应统一解包。 */
export async function call(name, body = {}) {
  const res = await pyInvoke(name, body);
  return unwrap(res);
}

function unwrap(res) {
  if (Array.isArray(res)) return res;
  if (res && typeof res === "object" && "root" in res && Object.keys(res).length === 1) {
    return res.root;
  }
  return res;
}

/** 订阅后端 py:// 事件，返回取消函数。 */
export function listenPy(name, cb) {
  return listen(`py://${name}`, (e) => cb(e.payload));
}
