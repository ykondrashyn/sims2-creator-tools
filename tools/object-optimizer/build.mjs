import { build } from 'esbuild';
import fs from 'node:fs/promises';
import path from 'node:path';
const dir = new URL('.', import.meta.url).pathname;
const out = path.resolve(process.env.PROJECT_VENDOR_OUT || path.join(dir, '../../dist/static/vendor'));
await fs.mkdir(out, {recursive:true});
const result = await build({absWorkingDir:dir, entryPoints:['worker.mjs'], bundle:true, format:'esm',
  platform:'browser', target:['firefox120','chrome120'], minify:true, legalComments:'eof',
  alias:{'ndarray-pixels':path.join(dir,'no-image-processing.mjs')},
  // These image utilities are unused by geometry transforms. Their CommonJS
  // initializers otherwise survive tree shaking and include dynamic JS eval.
  plugins:[{name:'unused-image-utilities', setup(b) {
    b.onResolve({filter:/^(ndarray|ndarray-pixels|ndarray-lanczos|ktx-parse)$/}, async args => {
      if (args.pluginData?.resolved) return;
      const resolved = await b.resolve(args.path, {importer:args.importer, resolveDir:args.resolveDir,
        kind:args.kind, namespace:args.namespace, pluginData:{resolved:true}});
      return {...resolved, sideEffects:false};
    });
  }}],
  external:['node:*'], metafile:true, outfile:path.join(out, 'object-optimizer.js')});
const code = await fs.readFile(path.join(out, 'object-optimizer.js'), 'utf8');
if (/\bnew Function\b|\beval\(/.test(code)) throw new Error('Optimizer bundle must not require JavaScript eval.');
// The standalone archive asset pins its codec with each saved batch. Bundle
// only fflate's synchronous streaming codec, without its generated workers.
const archive = await build({absWorkingDir:dir,
  entryPoints:[process.env.PROJECT_ARCHIVE_SOURCE || '../../web/src/package-runtime/zip.ts'],
  nodePaths:[path.join(dir,'node_modules')], bundle:true, format:'esm',
  platform:'browser', target:['firefox109','chrome109'], minify:true,
  legalComments:'eof', metafile:true, outfile:path.join(out,'package-archive.js')});
const archiveCode = await fs.readFile(path.join(out,'package-archive.js'),'utf8');
if (/\bnew Function\b|\beval\(/.test(archiveCode))
  throw new Error('Archive bundle must not require JavaScript eval.');
const packages = new Map();
for (const file of new Set([...Object.keys(result.metafile.inputs),...Object.keys(archive.metafile.inputs)])) {
  if (!file.includes('node_modules/')) continue;
  let folder = path.dirname(path.resolve(dir, file));
  while (path.dirname(folder) !== folder) {
    try {
      const pkg = JSON.parse(await fs.readFile(path.join(folder, 'package.json'), 'utf8'));
      if (pkg.name) {packages.set(folder,pkg); break;}
    } catch {}
    folder = path.dirname(folder);
  }
}
let notices = 'Object optimizer and package archive dependencies\n\n';
for (const [folder, pkg] of [...packages.entries()].sort()) {
  notices += `${pkg.name} ${pkg.version}\n`;
  for (const file of await fs.readdir(folder)) if (/^licen[cs]e/i.test(file)) notices += await fs.readFile(path.join(folder,file),'utf8');
  notices += '\n\n';
}
await fs.writeFile(path.join(out,'OBJECT-OPTIMIZER-LICENSES.txt'), notices);
await fs.writeFile(path.join(out,'vendor-sources.json'), JSON.stringify([...packages.keys()].map(p=>path.relative(dir,p)),null,2)+'\n');
