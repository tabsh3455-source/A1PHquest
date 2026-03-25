import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import AutoImport from "unplugin-auto-import/vite";
import Components from "unplugin-vue-components/vite";
import { ElementPlusResolver } from "unplugin-vue-components/resolvers";

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({
      resolvers: [ElementPlusResolver({ importStyle: "css" })],
      dts: false,
      vueTemplate: true
    }),
    Components({
      resolvers: [ElementPlusResolver({ importStyle: "css" })],
      dts: false,
      directives: true
    })
  ],
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
