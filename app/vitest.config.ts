import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'node:url';

/**
 * Unit tests cover the pure modules only -- reconnection, state reduction and
 * formatting. Those carry the reliability rules, and none of them import React
 * Native, so they run in plain Node without a native toolchain.
 *
 * Anything touching the camera or native modules is validated on a physical
 * device instead; see docs/roadmap.md.
 */
export default defineConfig({
  resolve: {
    alias: {
      '@chessview/protocol': fileURLToPath(
        new URL('../packages/protocol/src/index.ts', import.meta.url),
      ),
      '~': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
