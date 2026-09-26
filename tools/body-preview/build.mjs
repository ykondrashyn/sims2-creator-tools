import { build } from 'esbuild';
import { mkdir, copyFile } from 'node:fs/promises';
import path from 'node:path';
const root = new URL('../..', import.meta.url).pathname;
const out = process.env.PROJECT_VENDOR_OUT || path.join(root, 'dist/static/vendor');
await mkdir(out, {recursive: true});
await build({entryPoints:[new URL('./vendor-entry.js',import.meta.url).pathname], bundle:true,
  format:'esm', minify:true, legalComments:'eof', target:['chrome109','firefox109'],
  outfile:path.join(out,'three-preview.js')});
await copyFile(new URL('../LICENSE', import.meta.resolve('three')).pathname,path.join(out,'THREE-LICENSE.txt'));
