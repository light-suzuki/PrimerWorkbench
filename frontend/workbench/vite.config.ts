import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";

const host = process.env.WORKBENCH_HOST || "127.0.0.1";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    host,
  },
  preview: {
    port: 5173,
    strictPort: true,
    host,
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom"],
        },
      },
    },
  },
});
