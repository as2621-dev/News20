import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    // Reason: the repo also holds a Python pytest tree under tests/agents/**.
    // Scope Vitest to the TS test trees (tests/lib/** + tests/seed/**) ONLY so it
    // never tries to parse Python files under tests/agents/**.
    include: ["tests/lib/**/*.test.{ts,tsx}", "tests/seed/**/*.test.{ts,tsx}"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
