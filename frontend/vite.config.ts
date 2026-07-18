import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// AIRIS frontend — Milestone 0 bootstrap config.
// No app-specific aliasing/proxying added yet beyond the API base URL,
// which components read from import.meta.env at call time (see
// frontend/src/lib/api.ts once it exists in a later milestone).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
});
