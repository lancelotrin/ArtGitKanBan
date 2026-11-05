import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

type ShowToast = (message: string, bad?: boolean) => void;

const ToastContext = createContext<ShowToast>(() => {});

// className：心愿墙沿用自己的 .toast 样式，其它页面用 base.css 里的 .app-toast。
export function ToastProvider({ children, className = "app-toast" }: { children: ReactNode; className?: string }) {
  const [toast, setToast] = useState({ message: "", bad: false, show: false });
  const timer = useRef<number>(0);
  const show = useCallback<ShowToast>((message, bad = false) => {
    setToast({ message, bad, show: true });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setToast((t) => ({ ...t, show: false })), bad ? 4000 : 2400);
  }, []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className={className + (toast.show ? " show" : "") + (toast.bad ? " bad" : "")} role="status">
        {toast.message}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
