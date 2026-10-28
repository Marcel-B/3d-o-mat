import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Beim Entwickeln (npm run dev) gehen /api-Aufrufe an die Python-App auf Port 8090.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8090' } },
  build: { chunkSizeWarningLimit: 1500 },
});
