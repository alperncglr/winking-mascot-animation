// @lovable.dev/vite-tanstack-config already includes the following — do NOT add them manually
// or the app will break with duplicate plugins:
//   - TanStack devtools (dev-only, first), tanstackStart, viteReact, tailwindcss, tsConfigPaths,
//     nitro (build-only using cloudflare as a default target), VITE_* env injection, @ path alias,
//     React/TanStack dedupe, error logger plugins, and sandbox detection (port/host/strictPort).
// You can pass additional config via defineConfig({ vite: { ... }, etc... }) if needed.
import { defineConfig } from "@lovable.dev/vite-tanstack-config";
import { loadEnv } from "vite";

export default defineConfig({
  vite: {
    plugins: [{
      name: "reject-public-backend-api-key",
      config(_config, { command, mode }) {
        if (command === "build" && loadEnv(mode, process.cwd(), "VITE_")["VITE_MEETING_SCRIBE_API_KEY"]?.trim()) {
          throw new Error("VITE_MEETING_SCRIBE_API_KEY must not be set: it exposes the backend key to browsers. Configure the reverse proxy instead.");
        }
      },
    }],
  },
  tanstackStart: {
    // Redirect TanStack Start's bundled server entry to src/server.ts (our SSR error wrapper).
    // nitro/vite builds from this
    server: { entry: "server" },
  },
  // A4000 dağıtımı Cloudflare Workers değil, düz bir Node süreci olarak
  // çalışacağından varsayılan cloudflare-module preset yerine node-server
  // hedefleniyor (çıktı: .output/server/index.mjs, `node` ile çalıştırılır).
  nitro: {
    preset: "node-server",
  },
});
