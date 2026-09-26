import {artifact} from "./artifacts.mjs";
// Replay representative fixed identities with resource compression disabled.
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
const out=process.env.REFPACK_PARITY_OUTPUT||'artifacts/refpack-option/hair-tattoo-off';
const dir=artifact("PACKAGE_RUNTIME_ASSET_ROOT");
const source=JSON.parse(await fs.readFile('artifacts/texture-encoders/final/directxtex/parity-plan.json'));
const m=JSON.parse(await fs.readFile(dir+'/manifest.json'));
const asset=n=>fs.readFile(dir+'/'+m.assets[n].sha256);
const glue=await import('data:text/javascript;base64,'+(await asset('glue')).toString('base64'));
const wasm=await glue.default({module_or_path:await asset('wasm')}),e=new glue.BrowserEngine();
for(const[k,p]of Object.entries(source.assets))e.put_asset(k,await fs.readFile(p));
const wanted=new Set(['Bun_Dynamite.package','Bun_Shockwave.package','Bun_MailBomb.package','pooklet-mg-swirl.package','Rose_Dynamite.package','Rose_custom_00000000000000000000000000000000.package','parity-am.package','parity-af.package','parity-am-af.package','twenty-tattoos.package']);
const requests=source.requests.filter(r=>!r.output||wanted.has(r.output));
await fs.mkdir(out+'/wasm-parity',{recursive:true});const results=[];
for(const r of requests){
 if(r.output)r.params.job.refpack_compression=false;
 const report=JSON.parse(e.call(JSON.stringify(r)));
 if(r.output){
  assert.equal(report.package_compression.refpack_enabled,false);
  assert.equal(report.package_compression.compressed_resources,0);
  const bytes=e.take_asset('output');
  await fs.writeFile(out+'/wasm-parity/'+r.output,bytes);
  results.push({name:r.output,bytes:bytes.length,report});
  console.log(r.output,bytes.length,'uncompressed');
 }
}
await fs.writeFile(out+'/plan.json',JSON.stringify({assets:source.assets,requests}));
execFileSync(artifact("NATIVE_RUNTIME"),[out+'/plan.json',out+'/native-parity'],{stdio:'inherit',timeout:600000});
for(const r of results)assert.deepEqual(await fs.readFile(out+'/wasm-parity/'+r.name),await fs.readFile(out+'/native-parity/'+r.name));
await fs.writeFile(out+'/results.json',JSON.stringify({release:m.release,heap_bytes:wasm.memory.buffer.byteLength,results},null,2));
console.log('PASS',results.length,'disabled-mode native/WASM packages match exactly');
