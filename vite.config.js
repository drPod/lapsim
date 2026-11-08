import { resolve } from 'node:path';
import { defineConfig } from 'vite';
import basicSsl from '@vitejs/plugin-basic-ssl';

// `npm run dev` serves the client over HTTPS (self-signed via @vitejs/plugin-basic-ssl)
// on :5173 so the Quest browser gets a secure context for WebXR. Set NO_SSL=1 to disable
// (e.g. behind a TLS-terminating tunnel). API, WebSocket and MediaMTX calls go to the
// Node event server on :8787.
const api = `http://127.0.0.1:${process.env.PORT || 8787}`;

export default defineConfig({
  root: 'client',
  publicDir: 'public',
  plugins: process.env.NO_SSL ? [] : [basicSsl({ name: 'lap-sim-vr' })],
  server: {
    host: true,
    port: 5173,
    allowedHosts: true,
    proxy: {
      '/api': api,
      '/mediamtx': api,
      '/ws': { target: api.replace('http', 'ws'), ws: true },
    },
  },
  build: {
    outDir: '../dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 2000,
    rollupOptions: {
      input: {
        main: resolve(import.meta.dirname, 'client/index.html'),
        control: resolve(import.meta.dirname, 'client/control.html'),
        sender: resolve(import.meta.dirname, 'client/sender.html'),
        abdomen: resolve(import.meta.dirname, 'client/abdomen.html'),
        credits: resolve(import.meta.dirname, 'client/credits.html'),
        dashboard: resolve(import.meta.dirname, 'client/dashboard.html'),
      },
    },
  },
});
