import eslintReact from '@eslint-react/eslint-plugin'
import js from '@eslint/js'
import tsPlugin from '@typescript-eslint/eslint-plugin'
import tsParser from '@typescript-eslint/parser'
import { createTypeScriptImportResolver } from 'eslint-import-resolver-typescript'
import { defineConfig } from 'eslint/config'
import { createNodeResolver, importX } from 'eslint-plugin-import-x'
import reactHooks from 'eslint-plugin-react-hooks'
import globals from 'globals'

// Keep the prior React-recommended checks without opting into newer compiler heuristics.
const reactPlugin = eslintReact.configs['recommended-typescript'].plugins['@eslint-react']
const reactRuleNames = [
  '@eslint-react/no-component-will-mount',
  '@eslint-react/no-component-will-receive-props',
  '@eslint-react/no-component-will-update',
  '@eslint-react/no-direct-mutation-state',
  '@eslint-react/no-missing-key',
  '@eslint-react/jsx-no-comment-textnodes',
  '@eslint-react/jsx-no-children-prop',
  '@eslint-react/jsx-no-children-prop-with-children',
  '@eslint-react/dom-no-dangerously-set-innerhtml',
  '@eslint-react/dom-no-dangerously-set-innerhtml-with-children',
  '@eslint-react/dom-no-find-dom-node',
  '@eslint-react/dom-no-render',
  '@eslint-react/dom-no-render-return-value',
  '@eslint-react/dom-no-unknown-property',
  '@eslint-react/dom-no-unsafe-target-blank',
]
const reactRules = Object.fromEntries(
  Object.entries(eslintReact.configs['recommended-typescript'].rules).filter(([rule]) =>
    reactRuleNames.includes(rule)
  )
)

export default defineConfig([
  {
    files: ['src/**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tsPlugin.configs['flat/recommended'],
      importX.flatConfigs.recommended,
      importX.flatConfigs.typescript,
    ],
    plugins: {
      '@eslint-react': reactPlugin,
      'react-hooks': reactHooks,
    },
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      parser: tsParser,
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    settings: {
      'react-x': { version: 'detect' },
      'import-x/resolver-next': [
        createTypeScriptImportResolver({ alwaysTryTypes: true }),
        createNodeResolver(),
      ],
    },
    rules: {
      ...reactRules,
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      'import-x/order': [
        'warn',
        {
          groups: ['builtin', 'external', 'internal', 'parent', 'sibling', 'index'],
          'newlines-between': 'always',
          alphabetize: {
            order: 'asc',
            caseInsensitive: true,
          },
        },
      ],
    },
  },
])
