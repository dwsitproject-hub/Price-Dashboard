import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv, type Plugin } from 'vite';

/**
 * Injects the DWS Hub SDK <script> into <head> before the app bundle — only when an SDK key is
 * configured (PRD SDK-01). data-release = git SHA so Sentinel reads the exact code that ran.
 * The guide's inline `window.DwsHub = …` line and `onerror` attribute are left out: the CSP allows no
 * inline script. src/lib/hubSdk.ts creates the queue and reports a script that failed to load.
 */
function hubSdk(env: Record<string, string>): Plugin {
  return {
    name: 'aastar-hub-sdk',
    transformIndexHtml(html) {
      const key = env.VITE_HUB_SDK_KEY?.trim();
      const url = env.VITE_HUB_SDK_URL?.trim();
      if (!key || !url) return html;
      const esc = (s: string) => s.replace(/[&"<>]/g, (c) => `&#${c.charCodeAt(0)};`);
      const tag =
        `<script src="${esc(url)}" data-app-key="${esc(key)}" data-release="${esc(env.VITE_GIT_SHA || 'dev')}" ` +
        `data-bug-button="true" data-bug-button-position="bottom-right" defer></script>`;
      return html.replace('<!-- hub-sdk -->', tag);
    },
  };
}

export default defineConfig(({ mode }) => {
  const env = { ...process.env, ...loadEnv(mode, process.cwd(), 'VITE_') } as Record<string, string>;
  return {
    plugins: [react(), hubSdk(env)],
    server: {
      host: '127.0.0.1',
      port: 5181,
      strictPort: true,
      proxy: {
        // Same origin in dev as in prod (Nginx): cookies are first-party, no CORS.
        '/api': { target: 'http://127.0.0.1:8710', changeOrigin: false, xfwd: true },
      },
    },
    preview: { host: '127.0.0.1', port: 5182, strictPort: true,
               proxy: { '/api': { target: 'http://127.0.0.1:8710', xfwd: true } } },
    esbuild: { keepNames: true }, // readable stack traces for Sentinel (SDK-07)
    build: {
      sourcemap: 'hidden',
      chunkSizeWarningLimit: 1500,
      rollupOptions: { output: { manualChunks: { react: ['react', 'react-dom', 'react-router-dom'] } } },
    },
  };
});
