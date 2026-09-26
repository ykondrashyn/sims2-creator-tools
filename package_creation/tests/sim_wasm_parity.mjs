import {artifact} from "./artifacts.mjs";
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
const root=process.cwd(),dir=artifact("PACKAGE_RUNTIME_ASSET_ROOT", process.argv[2]);
const out=path.resolve('artifacts/sim-creator/parity');await fs.mkdir(out,{recursive:true});
const m=JSON.parse(await fs.readFile(path.join(dir,'manifest.json')));
const get=n=>fs.readFile(path.join(dir,m.assets[n].sha256));
const glue=await import('data:text/javascript;base64,'+(await get('glue')).toString('base64'));
const wasm=await glue.default({module_or_path:await get('wasm')});
const reports=[];
for(const item of m.sims.items){
  const e=new glue.BrowserEngine();
  const assets={'model.glb':path.join(root,'artifacts/sim-creator/model.glb'),'reference':path.join(dir,m.assets[item.asset].sha256)};
  for(const [k,p]of Object.entries(assets))e.put_asset(k,await fs.readFile(p));
  const call=(op,params)=>JSON.parse(e.call(JSON.stringify({version:1,op,params})));
  for(const rotation of [0,45,90]){
    const params={model:'model.glb',reference:'reference',neck:.84,rotation};
    const started=performance.now(), result=call('sim_fit',params);
    const native=JSON.parse(execFileSync(process.env.NATIVE_SIM,{input:JSON.stringify({assets,op:'sim_fit',params}),encoding:'utf8',maxBuffer:16*1024**2}));
    assert.deepEqual(native,result);
    assert.deepEqual(call('sim_preview',params),result);
    assert.throws(()=>call('sim_build',params),/persistent-head/);
    reports.push({body:item.id,rotation,milliseconds:performance.now()-started,heap_bytes:wasm.memory.buffer.byteLength,parts:result.parts.length});
    console.log(item.id,rotation,'native/WASM fitting identical, download gate closed');
  }
  e.free();
}
await fs.writeFile(path.join(out,'report.json'),JSON.stringify({cases:reports.length,results:reports,gameplay:'not_tested'},null,2));
