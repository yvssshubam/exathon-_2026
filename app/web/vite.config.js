// The console runs on http://localhost:5173 and forwards every /api call to the API on port 8000,
// so the browser only ever talks to one address.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});