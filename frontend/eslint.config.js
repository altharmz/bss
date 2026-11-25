import js from '@eslint/js';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['.coverage', '.shots', 'dist', 'node_modules', 'src/api/generated'] },
  js.configs.recommended,
  ...tseslint.configs.strict,
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: { globals: globals.browser },
    plugins: { 'react-hooks': reactHooks },
    rules: reactHooks.configs.recommended.rules,
  },
  {
    files: [
      'scripts/**/*.mjs',
      'vite.config.ts',
      'eslint.config.js',
      'playwright.config.ts',
      'playwright.shots.config.ts',
      'e2e-shots/**/*.ts',
      'e2e/**/*.ts',
    ],
    languageOptions: { globals: globals.node },
  },
);
