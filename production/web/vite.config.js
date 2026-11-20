import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  // Relative asset paths: the bundle must load from a file:// URL in
  // the desktop shell as well as from the web server.
  base: './',
  plugins: [react()],
  server: {
    // Local dev only: forward /api to the FastAPI backend on :8000.
    proxy: { "/api": "http://localhost:8000" },
  },
});
