import { resolve } from "node:path";
import { defineConfig } from "vite";

export default defineConfig({
  build: {
    rollupOptions: {
      input: {
        main: resolve(process.cwd(), "index.html"),
        interview: resolve(process.cwd(), "interview.html"),
        ledger: resolve(process.cwd(), "interview-ledger.html"),
        interviewAdmin: resolve(process.cwd(), "interview-admin.html"),
      },
    },
  },
});
