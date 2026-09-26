import tseslint from 'typescript-eslint';
export default tseslint.config(
 {ignores:['**/vendor/**','**/generated/**','**/node_modules/**','**/dist/**','**/artifacts/**']},
 ...tseslint.configs.recommended,
 {files:['web/src/**/*.ts'],languageOptions:{parserOptions:{project:'./web/tsconfig.json'}},rules:{
  // Protocol v1 carries opaque, engine-validated feature dictionaries. Do not
  // pretend their unknown extension keys are known to this website release.
  '@typescript-eslint/no-explicit-any':'off',
  '@typescript-eslint/no-unused-vars':['error',{argsIgnorePattern:'^_',varsIgnorePattern:'^_',caughtErrorsIgnorePattern:'^_',ignoreRestSiblings:true}],
  '@typescript-eslint/no-empty-object-type':'off',
  '@typescript-eslint/no-unsafe-function-type':'error',
  '@typescript-eslint/no-shadow':['error',{ignoreTypeValueShadow:true}],
  '@typescript-eslint/no-floating-promises':['error',{ignoreVoid:true,ignoreIIFE:true}],
  'no-empty':['error',{allowEmptyCatch:true}],
 }},
);
