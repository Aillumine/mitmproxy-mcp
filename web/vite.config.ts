/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: resolve(__dirname, '../src/mitm_proxy_mcp/webui'),
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/v1': { target: 'http://127.0.0.1:18765', changeOrigin: false },
    },
  },
  test: { environment: 'node' },
});
