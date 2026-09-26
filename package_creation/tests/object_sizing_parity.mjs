// Native/WASM structural acceptance, deliberately separate from gameplay.
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import * as THREE from '../../tools/body-preview/node_modules/three/build/three.module.js';
import {GLTFLoader} from '../../tools/body-preview/node_modules/three/examples/jsm/loaders/GLTFLoader.js';
const root=process.cwd(),out=path.resolve(process.env.OBJECT_SIZING_ARTIFACTS||'artifacts/object-sizing'),runtime=path.resolve(process.argv[2]||path.join(out,'runtime'));
const m=JSON.parse(await fs.readFile(path.join(runtime,'manifest.json')));
const read=n=>fs.readFile(path.join(runtime,m.assets[n].sha256));
const hash=b=>createHash('sha256').update(b).digest('hex');
const glue=await import('data:text/javascript;base64,'+(await read('glue')).toString('base64'));
await glue.default({module_or_path:await read('wasm')});const engine=new glue.BrowserEngine();
const call=(op,params)=>JSON.parse(engine.call(JSON.stringify({version:1,op,params})));
const native=process.env.NATIVE_OBJECT;
const assets={'object-catalog':path.join(runtime,m.assets['object-catalog'].sha256),'object-game':path.join(runtime,m.assets['object-game'].sha256)};
const nativeCall=(op,params,output)=>JSON.parse(execFileSync(native,{input:JSON.stringify({op,params,assets,output}),encoding:'utf8',maxBuffer:128*1024**2}));
const report=[];
const job={id:'ad01234567890123456789012345678912',creator:'ScaleTest',object_name:'Scale',title:'Scale validation',description:'Isolated validation object',price:10,mode:'model',sizing_version:2,model_file:'model.glb'};
for(const file of ['artifacts/object-creator/model.glb','artifacts/object-optimizer/ronald_browser_optimized.glb']){
  const ronald=file.includes('ronald');
  for(const template of m.objects.items.filter(t=>t.kind.endsWith('decor') && (!ronald || ['urn','chi'].includes(t.id)))){
    call('reset_inputs',{});
    for(const n of ['object-catalog','object-game'])engine.put_asset(n,await read(n));
    const source=await read(template.asset);
    engine.put_asset('selected-object',source);call('object_inspect',{files:['selected-object'],trusted:true});
    const normalized=engine.take_asset('object-template');engine.put_asset('object-template',normalized);
    const sourceFile=path.join(out,`${template.id}-normalized.package`);await fs.writeFile(sourceFile,normalized);assets['object-template']=sourceFile;
    const bytes=await fs.readFile(file);engine.put_asset('model.glb',bytes);assets['model.glb']=path.resolve(file);
    const fixed=process.env.OBJECT_FIXED_FIT_ONLY?call('object_layout',{job:{...job,rotation:37,fit_to_template:true}}):null;
    const cases=fixed?[['fixed',0],['fixed',37],['fixed',90]]:[...(process.env.OBJECT_FIT_ONLY?[[100,0]]:[...(ronald?[[100,0],[100,90]]:[[25,0],[100,37],[150,-90]]),['original',0],['original',90]]),['fit',0],['fit',37],['fit',90]];
    for(const [percent,rotation] of cases){
      const j={...job,object_name:template.id.replaceAll('-',''),target_height:fixed?fixed.height:['original','fit'].includes(percent)?template.dimensions.height:m.objects.reference.reference_height*percent/100,rotation,fit_to_template:percent==='fit',height_from_fit:percent==='fixed'};
      const start=performance.now();const layout=call('object_layout',{job:j});
      assert.equal(layout.reference_height,m.objects.reference.reference_height);
      assert.deepEqual(layout,nativeCall('object_layout',{job:j}));
      if(fixed){assert.equal(layout.height,fixed.height);assert.equal(layout.scale,fixed.scale);assert.equal(layout.fit_to_template,false);assert.equal(layout.height_from_fit,true);}
      if(percent==='fit') {
        assert.equal(layout.requires_acknowledgement,false);
        assert.ok(layout.height<=template.dimensions.height);
        assert.equal(layout.fit_to_template,true);
        const a=layout.game_min,b=layout.game_max;
        for(let x=a[0]+1e-5;x<b[0];x+=(b[0]-a[0])/20) for(let y=a[1]+1e-5;y<b[1];y+=(b[1]-a[1])/20)
          assert.ok(layout.placement.tiles.some(t=>Math.abs(x-t[0])<=.50001 && Math.abs(y-t[1])<=.50001));
      } else assert.ok(Math.abs(layout.dimensions.height-j.target_height)<1e-5);
      assert.equal(layout.placement.tile_count,{urn:1,'fruit-bowl':1,venus:1,chimes:2,chi:4}[template.id]);
      j.placement_ack=layout.signature;
      const preview=call('object_preview',{job:j});const built=engine.take_asset('output');
      assert.equal(preview.layout.signature,layout.signature);
      const target=path.join(out,`${ronald?'Ronald':'Fixture'}_${template.id}_${percent}_${rotation}.package`);
      const expected=nativeCall('object_build',{job:j},target);
      assert.deepEqual(preview.report,expected);assert.equal(hash(built),hash(await fs.readFile(target)));
      call('object_build',{job:j});assert.equal(hash(engine.take_asset('output')),hash(built));
      assert.equal(preview.converted.meshes.length,1,'multi-tile templates must have one visible model');
      const scene=(await new GLTFLoader().parseAsync(Uint8Array.from(Buffer.from(preview.converted.meshes[0].glb,'base64')).buffer,'')).scene;
      scene.updateMatrixWorld(true);const box=new THREE.Box3(),selection=new THREE.Box3();
      scene.traverse(o=>{if(!o.isMesh)return;const result=o.name==='b_mesh'?selection:box;const p=new THREE.Vector3(),a=o.geometry.attributes.position;
        for(const i of o.geometry.index?.array||Array.from({length:a.count},(_,i)=>i))result.expandByPoint(p.fromBufferAttribute(a,i).applyMatrix4(o.matrixWorld));});
      assert.ok(Math.abs(box.max.y-box.min.y-layout.height)<1e-5);
      assert.ok(box.min.distanceTo(selection.min)<1e-5 && box.max.distanceTo(selection.max)<1e-5);
      const modelTextures=expected.textures.filter(t=>t.name.includes('-model-'));
      assert.ok(modelTextures.every(t=>t.format==='DXT3' && t.mips===1+Math.floor(Math.log2(Math.max(t.width,t.height)))));
      assert.equal(new Set(expected.objects.map(o=>o.guid)).size,expected.objects.length);
      const expectedTiles={urn:[0],'fruit-bowl':[0],venus:[0],chimes:[65535,0,256],chi:[65535,0,1,256,257]}[template.id];
      assert.deepEqual(expected.objects.map(o=>o.tile).sort((a,b)=>a-b),expectedTiles.sort((a,b)=>a-b));
      report.push({model:ronald?'Ronald':'fixture',template:template.id,percent,rotation,dimensions:layout.dimensions,warning:layout.requires_acknowledgement,bytes:built.length,sha256:hash(built),milliseconds:performance.now()-start});
      console.log(`${ronald?'Ronald':'Fixture'} ${template.id} ${percent}% ${rotation} degrees passed`);
    }
  }
}
await fs.writeFile(path.join(out,'sizing-parity.json'),JSON.stringify(report,null,2));
