import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
const root=process.cwd(),dir=path.resolve(process.env.PACKAGE_RUNTIME_ASSET_ROOT),out=path.resolve(process.env.SIM_GUIDED_PARITY_OUTPUT || 'artifacts/sim-creator/guided-parity');await fs.mkdir(out,{recursive:true});
const m=JSON.parse(await fs.readFile(path.join(dir,'manifest.json')));
const get=n=>fs.readFile(path.join(dir,m.assets[n].sha256));
const glue=await import('data:text/javascript;base64,'+(await get('glue')).toString('base64'));
const wasm=await glue.default({module_or_path:await get('wasm')});const reports=[];
async function run(model,body,large=false){
 const engine=new glue.BrowserEngine();const item=m.sims.items.find(i=>i.id===body);
 const assets={'model.glb':model,reference:path.join(dir,m.assets[item.asset].sha256)};
 for(const[n,p]of Object.entries(assets))engine.put_asset(n,await fs.readFile(p));
 const call=(op,params)=>JSON.parse(engine.call(JSON.stringify({version:1,op,params})));
 const p={model:'model.glb',reference:'reference',guided_version:2,alignment:[0,0,0],pose:'t',include_images:false};
 let before;
 for(const op of ['sim_align','sim_guided_fit']){
   if(op==='sim_guided_fit')Object.assign(p,{markers:before.suggestions,neck_height:before.suggestions.neck[2],review:{align:true,markers:true,head:true}});
   const started=performance.now(),result=call(op,p),ms=performance.now()-started,bytes=engine.take_asset('sim-buffer');
   if(op==='sim_align')before=result;
   const file=path.join(out,`${body}-${large?'large':'small'}-${op}.bin`);
   const native=JSON.parse(execFileSync(process.env.NATIVE_SIM,{input:JSON.stringify({op,assets,params:p,output:file,output_asset:'sim-buffer'}),encoding:'utf8',maxBuffer:32*1024**2,timeout:600000}));
   assert.deepEqual(result,native);assert.deepEqual(Buffer.from(bytes),await fs.readFile(file));
   if(!large){assert.deepEqual(call(op,p),result);assert.deepEqual(engine.take_asset('sim-buffer'),bytes);}
   reports.push({body,large,op,milliseconds:ms,heap_bytes:wasm.memory.buffer.byteLength,buffer_bytes:bytes.length});console.log(body,large?'Ronald':'humanoid',op,'native/WASM metadata and buffers match',Math.round(ms)+'ms');
 }
 assert.throws(()=>call('sim_build',{}),/persistent-head/);engine.free();
}
for(const body of ['am','af'])await run(path.join(root,'artifacts/sim-creator/guided/humanoid.glb'),body);
if(process.argv.includes('--ronald'))await run(path.join(process.env.HOME,'Downloads/ronald_mcdonald.glb'),'am',true);
await fs.writeFile(path.join(out,'report.json'),JSON.stringify({release:m.release,results:reports,gameplay:'not_tested'},null,2));
