import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import react from "eslint-plugin-react";

export default [
  { ignores: ["dist/**", "node_modules/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      globals: globals.browser,
    },
    plugins: {
      react,
      "react-hooks": reactHooks,
    },
    settings: {
      react: {
        version: "detect",
      },
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // TypeScript がグローバルを解決するため、ESLint 側の no-undef は無効化。
      "no-undef": "off",
      "no-unused-vars": "off",
      "prefer-const": "warn",
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" },
      ],
      "@typescript-eslint/no-empty-object-type": "off",
      "@typescript-eslint/ban-ts-comment": "warn",
      "@typescript-eslint/no-unsafe-function-type": "off",
      "@typescript-eslint/no-unsafe-declaration-merging": "off",
    },
  },
];
