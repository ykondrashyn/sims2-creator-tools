import {artifact} from "./artifacts.mjs";
/** Compare each native and WASM block stream, including captured Body Shop bytes. */
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
const root=path.resolve(process.argv[2]||'artifacts/texture-encoders');
const engineRoot=artifact("TEXTURE_ENGINE_ROOT");
const native=artifact("TEXTURE_NATIVE");
const glue=await import('data:text/javascript;base64,'+(await fs.readFile(path.join(engineRoot,'engine.js'))).toString('base64'));
const wasmBytes=await fs.readFile(path.join(engineRoot,'engine_bg.wasm'));
assert(WebAssembly.Module.imports(new WebAssembly.Module(wasmBytes)).every(i=>!i.module.includes('wasi')),'WASI imports are forbidden');
const instance=await glue.default({module_or_path:wasmBytes});
const e=new glue.BrowserEngine();
const sha=b=>createHash('sha256').update(b).digest('hex');
const cases=JSON.parse(await fs.readFile(path.join(root,'corpus.json')));
const results=[];
await fs.mkdir(path.join(root,'codec-output'),{recursive:true});
for(const c of cases){
  const input=await fs.readFile(c.input);
  for(const format of c.formats||['DXT1','DXT3','DXT5'])for(const encoder of ['directxtex','bodyshop_dxt3','bodyshop']){
    const began=performance.now();
    e.put_asset('input',input);
    const report=JSON.parse(e.call(JSON.stringify({version:1,op:'encode_texture',params:{width:c.width,height:c.height,format,texture_encoder:encoder,asset:'input'}})));
    const bytes=e.take_asset('output');
    const wasmMs=performance.now()-began;
    const dest=path.join(root,'codec-output',`${c.name}-${format}-${encoder}.bin`);
    execFileSync(native,[encoder,format,String(c.width),String(c.height),c.input,dest]);
    const nativeBytes=await fs.readFile(dest);
    assert.equal(sha(bytes),sha(nativeBytes),`${c.name} ${format} ${encoder}: native/WASM difference`);
    if(format==='DXT3'&&['bodyshop_dxt3','bodyshop'].includes(encoder)&&c.expected){
      const expected=await fs.readFile(c.expected);
      assert.equal(sha(bytes),sha(expected),`${c.name}: Body Shop captured-byte difference`);
      if(c.expected_sha256)assert.equal(sha(expected),c.expected_sha256);
    }
    if(encoder==='bodyshop'&&c.expected_by_format?.[format]){
      const oracle=c.expected_by_format[format];
      const expected=await fs.readFile(oracle.path);
      assert.equal(sha(expected),oracle.sha256);
      assert.equal(sha(bytes),sha(expected),`${c.name} ${format}: recovered-codec difference`);
    }
    if(encoder==='bodyshop_dxt3'&&format!=='DXT3'){
      const legacyReference=await fs.readFile(path.join(root,'codec-output',`${c.name}-${format}-directxtex.bin`));
      assert.equal(sha(bytes),sha(legacyReference),'Legacy encoder routing changed');
    }
    results.push({name:c.name,format,encoder,bytes:bytes.length,sha256:sha(bytes),wasm_ms:wasmMs,heap_bytes:instance.memory.buffer.byteLength});
  }
  console.log(`${c.name}: native/WASM parity passed`);
}
await fs.writeFile(path.join(root,'codec-parity.json'),JSON.stringify({passed:true,cases:results.length,synthetic_blocks:cases.filter(c=>c.expected_type==='synthetic').reduce((n,c)=>n+c.random_blocks+c.structured_blocks,0),captured_textures:cases.filter(c=>c.expected&&c.expected_type!=='synthetic').length,captured_blocks:cases.filter(c=>c.expected&&c.expected_type!=='synthetic').reduce((n,c)=>n+Math.ceil(c.width/4)*Math.ceil(c.height/4),0),results},null,2));
e.free();
