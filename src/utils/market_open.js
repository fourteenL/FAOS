/** 上期所交易时段判断（日盘+夜盘，节假日无法本地判断——以柜台实际为准）。 */

function toSh(d = new Date()) {
  return { day: d.getDay(), mins: d.getHours() * 60 + d.getMinutes() };
}

export function isTradingTime(d = new Date()) {
  let { day, mins } = toSh(d);
  if (mins < 150) {
    day = (day + 6) % 7; // 凌晨属前一交易日夜盘（周六凌晨=周五夜盘 ✓、周日凌晨=周六无夜盘 ✓）
  }
  const weekday = day >= 1 && day <= 5;
  if (mins >= 21 * 60 || mins < 150) return weekday; // 夜盘 21:00–02:30
  const s1 = mins >= 9 * 60 && mins <= 10 * 60 + 15;
  const s2 = mins >= 10 * 60 + 30 && mins <= 11 * 60 + 30;
  const s3 = mins >= 13 * 60 + 30 && mins <= 15 * 60;
  return weekday && (s1 || s2 || s3);
}

/** 每 30 秒刷新的响应式开关（组件挂载调用，卸载清理）。 */
export function marketClock() {
  const ref = { value: isTradingTime() };
  const timer = setInterval(() => {
    ref.value = isTradingTime();
  }, 30000);
  ref.stop = () => clearInterval(timer);
  return ref;
}
