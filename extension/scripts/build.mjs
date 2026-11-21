import { build } from "vite";
import { cpSync, mkdirSync, rmSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const rootDir = resolve(__dirname, "..");
const distDir = resolve(rootDir, "dist");
const publicDir = resolve(rootDir, "public");

async function run() {
  if (existsSync(distDir)) {
    rmSync(distDir, { recursive: true, force: true });
  }
  mkdirSync(distDir, { recursive: true });

  // 1. Build popup HTML + TS + CSS
  await build({
    root: resolve(rootDir, "src/popup"),
    base: "./",
    configFile: false,
    logLevel: "warn",
    build: {
      outDir: distDir,
      emptyOutDir: false,
      rollupOptions: {
        input: resolve(rootDir, "src/popup/popup.html"),
        output: {
          entryFileNames: "popup.js",
          assetFileNames: "popup.[ext]",
        },
      },
    },
  });

  // 2. Build MV3 background service worker as a single ES module
  await build({
    root: rootDir,
    configFile: false,
    logLevel: "warn",
    build: {
      outDir: distDir,
      emptyOutDir: false,
      lib: {
        entry: resolve(rootDir, "src/background/serviceWorker.ts"),
        formats: ["es"],
        fileName: () => "background.js",
      },
      rollupOptions: {
        output: {
          inlineDynamicImports: true,
        },
      },
    },
  });

  // 3. Build universal content script as a single self-contained IIFE
  await build({
    root: rootDir,
    configFile: false,
    logLevel: "warn",
    build: {
      outDir: distDir,
      emptyOutDir: false,
      lib: {
        entry: resolve(rootDir, "src/content/index.ts"),
        name: "ScanlatorVogelsContent",
        formats: ["iife"],
        fileName: () => "content.js",
      },
      rollupOptions: {
        output: {
          inlineDynamicImports: true,
        },
      },
    },
  });

  // 4. Copy manifest.json & icons into dist/
  cpSync(publicDir, distDir, { recursive: true });
  console.log("[build] Extension Manifest V3 pronta em:", distDir);
}

run().catch((err) => {
  console.error("[build] Falha no build da extensao:", err);
  process.exit(1);
});
