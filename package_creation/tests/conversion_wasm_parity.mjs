import {artifact} from "./artifacts.mjs";
// Exact decoded RGBA comparison, including hidden RGB. No image thresholds.
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
const root=process.cwd(),runtime=artifact("PACKAGE_RUNTIME_ASSET_ROOT", process.argv[2]),corpus=path.resolve(process.argv[3]||'artifacts/body-conversion/corpus');
const manifest=JSON.parse(await fs.readFile(path.join(runtime,'manifest.json')));
const read=name=>fs.readFile(path.join(runtime,manifest.assets[name].sha256));
const glue=await import('data:text/javascript;base64,'+(await read('glue')).toString('base64'));
const wasm=await glue.default({module_or_path:await read('wasm')});
const output=path.resolve(process.env.CONVERSION_PARITY_OUTPUT||path.join(corpus,'wasm'));await fs.mkdir(output,{recursive:true});
const native=artifact("CONVERSION_NATIVE");
await fs.mkdir(path.join(output,"native"),{recursive:true});
const report=[];
for(const body of ['am','af']) for(const file of (await fs.readdir(corpus)).filter(f=>f.endsWith('.png')).sort()) {
  const engine=new glue.BrowserEngine();
  const call=(op,params={})=>JSON.parse(engine.call(JSON.stringify({version:1,op,params})));
  const source=await fs.readFile(path.join(corpus,file)),mapping=await read(`conversion:${body}`),start=performance.now();
  let result;
  // Repeat each conversion with new state and different bounded step sizes.
  for(const step of [4096,8192]) {
    engine.put_asset('input',source);engine.put_asset('mapping',mapping);
    call('conversion_validate',{input:'input'});
    call('conversion_begin',{input:'input',mapping:'mapping',body});
    let progress=0,s;
    do {s=call('conversion_step',{pixels:step});assert.ok(s.processed>=progress);progress=s.processed;}while(!s.done);
    const validation=call('conversion_finish');assert.equal(validation.validated,true);
    const bytes=Buffer.from(engine.take_asset('output'));
    if(result)assert.deepEqual(bytes,result,'Repeated WASM conversion changed');else result=bytes;
  }
  const dest=path.join(output,`${body}-${file}`);await fs.writeFile(dest,result);
  const nativeResult=path.join(output,'native',`${body}-${file}`);
  execFileSync(native,[body,path.join(runtime,manifest.assets[`conversion:${body}`].sha256),path.join(corpus,file),nativeResult]);
  const comparison=JSON.parse(execFileSync(path.join(root,'.venv/bin/python'),['-c',`import sys,json;from PIL import Image
images=[Image.open(p).convert('RGBA') for p in sys.argv[1:]]
a=images[0].tobytes();print(json.dumps({'dimensions':[im.size for im in images],'differing_bytes':[sum(x!=y for x,y in zip(a,im.tobytes())) for im in images[1:]]}))`,dest,nativeResult,path.join(corpus,'blender',`${body}-${file}`)],{encoding:'utf8'}));
  assert.deepEqual(comparison.dimensions,[[1024,1024],[1024,1024],[1024,1024]]);
  assert.deepEqual(comparison.differing_bytes,[0,0],`${body} ${file} decoded RGBA parity`);
  const row={body,file,decoded_differing_bytes:0,repeated_identical:true,milliseconds:Math.round(performance.now()-start),heap_bytes:wasm.memory.buffer.byteLength,sha256:createHash('sha256').update(result).digest('hex')};report.push(row);console.log(JSON.stringify(row));engine.free();
}
await fs.writeFile(path.join(output,'report.json'),JSON.stringify({release:manifest.release,comparisons:report},null,2));
