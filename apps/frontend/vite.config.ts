import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          const normalizedId = id.replace(/\\/g, "/");
          if (!normalizedId.includes("/node_modules/")) {
            return undefined;
          }
          if (
            normalizedId.includes("/node_modules/vue/") ||
            normalizedId.includes("/node_modules/@vue/") ||
            normalizedId.includes("/node_modules/vue-router/")
          ) {
            return "vue-core";
          }
          if (
            normalizedId.includes("/node_modules/element-plus/") ||
            normalizedId.includes("/node_modules/@element-plus-icons-vue/")
          ) {
            return "element-plus";
          }
          if (normalizedId.includes("/node_modules/lightweight-charts/")) {
            return "market-chart";
          }
          if (normalizedId.includes("/node_modules/axios/")) {
            return "http-client";
          }
          return undefined;
        }
      }
    }
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://api:8000",
        changeOrigin: true
      },
      "/ws": {
        target: "http://api:8000",
        changeOrigin: true,
        ws: true
      }
    }
  }
});
