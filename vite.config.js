import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
    watch: {
      ignored: ["**/src-tauri/**", "**/.venv/**"],
    },
  },
  build: {
    target: "chrome105",
    outDir: "dist",
  },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test-setup.js"],
    include: ["src/**/__tests__/**/*.test.js"],
  },
});
