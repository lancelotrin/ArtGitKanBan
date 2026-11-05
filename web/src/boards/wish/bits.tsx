import { nameColor } from "../../lib/format";

export function Avatar({ name }: { name: string }) {
  const shown = name || "?";
  return (
    <span className="av" style={{ background: nameColor(shown) }}>
      {shown.slice(0, 1)}
    </span>
  );
}

export function RoleTag({ role }: { role: string }) {
  const value = role || "其他";
  return <span className={"role r-" + value}>{value}</span>;
}

export const TAKE_CONFIRM = "确定接下这个心愿？接了大家会看到是你在做。";
