import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [tailwindcss(), react()],
  build: {
    // ponytail: outDir is under data/ so gh-pages publish_dir picks it up;
    // self-host without Node falls back to inline CSS + vanilla FILTER_JS/COMPOSE_JS
    outDir: "data/public/assets",
    emptyOutDir: true,
    manifest: true,
    assetsDir: "", // flat: /assets/<hash> not /assets/assets/<hash>
    rollupOptions: {
      input: {
        main: "web/src/main.tsx",
      },
    },
  },
});
