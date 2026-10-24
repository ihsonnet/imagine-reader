import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs separately on port 8000; the proxy keeps the
// session cookie same-origin. In production the API serves the built files.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
