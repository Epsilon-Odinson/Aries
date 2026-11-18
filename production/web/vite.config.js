import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    // Local dev only: forward /api to the FastAPI backend on :8000.
    proxy: { "/api": "http://localhost:8000" },
  },
});
