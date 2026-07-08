import React, { createContext, useCallback, useContext, useMemo, useState } from "react";

type ToastType = "info" | "success" | "error";

type Toast = {
  id: string;
  type: ToastType;
  message: string;
};

type ToastContextValue = {
  showToast: (message: string, type?: ToastType) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

export const useToast = (): ToastContextValue => {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast must be used within ToastProvider");
  }
  return ctx;
};

export const ToastProvider: React.FC<React.PropsWithChildren> = ({ children }) => {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const showToast = useCallback((message: string, type: ToastType = "info") => {
    const msg = message.trim();
    if (!msg) return;
    const id = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    setToasts((prev) => [...prev, { id, type, message: msg }]);
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 3200);
  }, []);

  const value = useMemo<ToastContextValue>(() => ({ showToast }), [showToast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-stack" aria-live="polite" aria-relevant="additions text">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.type}`}>
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
};

