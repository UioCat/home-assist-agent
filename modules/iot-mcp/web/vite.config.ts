import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    proxy: {
      "/agent-api": {
        target: "http://127.0.0.1:8080",
        rewrite: (path) => path.replace(/^\/agent-api/, "/api"),
      },
      "/api/v1": "http://127.0.0.1:8090",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
    globals: true,
  },
});
