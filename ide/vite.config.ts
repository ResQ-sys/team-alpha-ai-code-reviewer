import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The IDE shell talks to the FastAPI backend at :8010 through a dev proxy.
// All api.ts calls hit /api/* so nothing is hardcoded to a host.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8010',
        changeOrigin: true,
      },
    },
  },
});
