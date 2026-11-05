import { useEffect, type ReactNode } from "react";

// 遮罩 + 弹窗：点遮罩、按 Esc 关闭。样式类名沿用旧页面（.mask/.modal）。
export function Modal({ onClose, width, children }: { onClose: () => void; width?: number; children: ReactNode }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="mask show" onClick={(event) => event.target === event.currentTarget && onClose()}>
      <div className="modal" style={width ? { width: `min(${width}px,100%)` } : undefined}>
        {children}
      </div>
    </div>
  );
}
