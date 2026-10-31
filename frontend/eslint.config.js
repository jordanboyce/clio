import pluginVue from 'eslint-plugin-vue'

// Deliberately thin: vue's flat "recommended" preset plus a few noise
// filters. The goal is catching real breakage (undefined refs in templates,
// unused vars) during the store-layer refactors — not enforcing a style war.
export default [
  ...pluginVue.configs['flat/recommended'],
  {
    rules: {
      // The codebase predates these stylistic rules; enforcing them now
      // would bury real findings under thousands of formatting diffs.
      'vue/max-attributes-per-line': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/html-self-closing': 'off',
      'vue/html-indent': 'off',
      'vue/html-closing-bracket-newline': 'off',
      'vue/first-attribute-linebreak': 'off',
      'vue/attributes-order': 'off',
      'vue/multiline-html-element-content-newline': 'off',
      'vue/require-default-prop': 'off',
      // No router, no DOM custom-element ambiguity — single-word component
      // names (Toaster) conflict with nothing here.
      'vue/multi-word-component-names': 'off',
      // Keep the codebase where it is: strict equality (the `== null`
      // null-or-undefined idiom excepted) and block-scoped declarations.
      eqeqeq: ['error', 'always', { null: 'ignore' }],
      'no-var': 'error',
    },
  },
  {
    ignores: ['dist/**', 'node_modules/**'],
  },
]
