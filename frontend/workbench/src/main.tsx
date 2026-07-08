import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { ToastProvider } from "./components/ToastProvider";
import "./styles.css";

// React アプリケーションのエントリポイント
ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <ToastProvider>
      <App />
    </ToastProvider>
  </React.StrictMode>,
);
