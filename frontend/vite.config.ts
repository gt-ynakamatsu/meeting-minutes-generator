import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function viteBasePath(raw: string | undefined): string {
  const t = (raw ?? "").trim() || "/";
  if (t === "/") return "/";
  return t.endsWith("/") ? t : `${t}/`;
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, repoRoot, "");
  const apiProxy = env.VITE_DEV_API_PROXY || "http://127.0.0.1:8000";
  // Docker では frontend のみが /app にコピーされ、repoRoot の親は / になる。
  // loadEnv は /.env を見るだけで、Dockerfile の ENV VITE_BASE_PATH が読まれない。
  // ビルド時に compose から渡す ARG/ENV は process.env を優先する。
  const basePath = process.env.VITE_BASE_PATH ?? env.VITE_BASE_PATH;
  return {
    base: viteBasePath(basePath),
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: apiProxy,
          changeOrigin: true,
        },
      },
    },
  };
});
