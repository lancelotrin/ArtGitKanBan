import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

type ShowToast = (message: string, bad?: boolean) => void;

const ToastContext = createContext<ShowToast>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState({ message: "", bad: false, show: false });
  const timer = useRef<number>(0);
  const show = useCallback<ShowToast>((message, bad = false) => {
    setToast({ message, bad, show: true });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setToast((t) => ({ ...t, show: false })), 2400);
  }, []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className={"toast" + (toast.show ? " show" : "") + (toast.bad ? " bad" : "")} role="status">
        {toast.message}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
