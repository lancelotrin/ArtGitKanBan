// 头像颜色、相对时间：和旧页面的算法一致，同一个名字颜色不变。

const COLORS = ["#7c5cff", "#ff6f91", "#22a99b", "#f08c1a", "#4f7cff", "#d65db1", "#2f9e5b", "#c7832a"];

export function nameColor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return COLORS[hash % COLORS.length];
}

export function when(text: string): string {
  if (!text) return "";
  const date = new Date(text);
  const seconds = (Date.now() - date.getTime()) / 1000;
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return Math.floor(seconds / 60) + " 分钟前";
  if (seconds < 86400) return Math.floor(seconds / 3600) + " 小时前";
  if (seconds < 86400 * 7) return Math.floor(seconds / 86400) + " 天前";
  return date.getMonth() + 1 + "月" + date.getDate() + "日";
}
