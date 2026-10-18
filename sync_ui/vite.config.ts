import { defineConfig } from "vite";

// `npm run dev` serves the page with live reloading against a running `karaoke --sync`: set
// SYNC_URL to the address that prints, and the page's requests are passed on to it.
const sync = process.env.SYNC_URL ? new URL(process.env.SYNC_URL) : null;

export default defineConfig({
  base: "./",
  server: sync
    ? {
        proxy: {
          "^/(state|audio|save|close)$": {
            target: sync.origin,
            rewrite: (path) =>
              sync.pathname.replace(/\/?$/, "/") + path.slice(1),
          },
        },
      }
    : {},
  build: {
    // sync.py serves the page from the package and looks its files up by name, so the build
    // writes three flat files there: index.html, editor.js and editor.css. They are committed,
    // so running the CLI needs no Node. Not minified, so their diffs can be read.
    outDir: "../src/mvsep_karaoke/sync_page",
    emptyOutDir: true,
    assetsDir: "",
    minify: false,
    modulePreload: { polyfill: false },
    rolldownOptions: {
      output: {
        entryFileNames: "editor.js",
        assetFileNames: "editor[extname]",
      },
    },
  },
});
