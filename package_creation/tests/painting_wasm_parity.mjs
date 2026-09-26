import {artifact} from "./artifacts.mjs";
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
const root=process.cwd(),dir=artifact("PACKAGE_RUNTIME_ASSET_ROOT", process.argv[2]);
const out=path.resolve(process.env.PAINTING_PARITY_OUTPUT || 'artifacts/painting-creator/parity');await fs.mkdir(out,{recursive:true});
const m=JSON.parse(await fs.readFile(path.join(dir,'manifest.json')));
const get=n=>fs.readFile(path.join(dir,m.assets[n].sha256));
const glue=await import('data:text/javascript;base64,'+(await get('glue')).toString('base64'));
const wasm=await glue.default({module_or_path:await get('wasm')});
const hash=b=>createHash('sha256').update(b).digest('hex');
const reports=[];
for(const item of m.paintings.items){
  for(const extension of (process.env.PAINTING_PARITY_INPUTS||'png,jpg,webp').split(','))for(const mode of ['fill','contain']){
    const e=new glue.BrowserEngine();
    const assets={'painting-input':path.join(root,'artifacts/painting-creator/diagnostic.'+extension),'painting-template':path.join(dir,m.assets[item.asset].sha256),'painting-recipe':path.join(dir,m.assets[item.recipe_asset].sha256),'object-catalog':path.join(dir,m.assets['painting-catalog'].sha256),'object-game':path.join(dir,m.assets['object-game'].sha256)};
    for(const [k,p]of Object.entries(assets))e.put_asset(k,await fs.readFile(p));
    const call=(op,params)=>JSON.parse(e.call(JSON.stringify({version:1,op,params})));
    const job={texture_encoder:process.env.TEXTURE_ENCODER||"directxtex", refpack_compression: process.env.REFPACK_COMPRESSION !== "false",id:hash(item.id+extension+mode).slice(0,32),creator:'PaintingParity',title:item.id+' '+extension+' '+mode,description:'Original artwork: diagnostic validation',price:100,mode:'clone',crop:{mode,x:.3,y:.6,zoom:1.3,background:[220,230,240]}};
    if(extension==='png'&&mode==='contain')delete job.description;
    const started=performance.now();const prepared=call('painting_prepare',{job});const preview=call('painting_compose',{job});const report=call('painting_build',{job:prepared});const bytes=e.take_asset('output');
    assert.deepEqual(report.painting,preview.report);
    const nativePath=path.join(out,`${item.id}-${extension}-${mode}.package`);
    const native=JSON.parse(execFileSync(artifact("NATIVE_PAINTING"),{input:JSON.stringify({assets,op:'painting_build',params:{job:prepared},output:nativePath}),encoding:'utf8',maxBuffer:16*1024**2}));
    assert.deepEqual(native,report);assert.equal(hash(await fs.readFile(nativePath)),hash(bytes));
    call('painting_build',{job:prepared});assert.equal(hash(e.take_asset('output')),hash(bytes));
    reports.push({template:item.id,input:extension,mode,sha256:hash(bytes),bytes:bytes.length,milliseconds:performance.now()-started,heap_bytes:wasm.memory.buffer.byteLength,texture:report.painting});
    e.free();console.log(item.id,extension,mode,'native/WASM bytes identical');
  }
}
await fs.writeFile(path.join(out,'report.json'),JSON.stringify({cases:reports.length,results:reports},null,2));
