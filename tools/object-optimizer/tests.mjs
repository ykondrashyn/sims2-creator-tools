import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {Worker} from 'node:worker_threads';
import {zipSync} from 'fflate';
import {Document, WebIO, Logger} from '@gltf-transform/core';
import {optimize, readModel, LIMITS} from './optimizer.mjs';
const io = new WebIO().setLogger(new Logger(Logger.Verbosity.SILENT));
const originalFetch = globalThis.fetch;
globalThis.fetch = () => {throw new Error('Model processing attempted a network request');};
test.after(() => {globalThis.fetch = originalFetch;});

async function fixture(groups = 2, distinct = false) {
  const d = new Document(), b = d.createBuffer(), scene = d.createScene();
  d.getRoot().setDefaultScene(scene);
  d.getRoot().getAsset().extras = {author:'Example creator', license:'CC BY 4.0'};
  const position = d.createAccessor().setType('VEC3').setArray(new Float32Array([0,0,0, 1,0,0, 0,1,0])).setBuffer(b);
  const uv = d.createAccessor().setType('VEC2').setArray(new Float32Array([0,0, 1,0, 0,1])).setBuffer(b);
  const normal = d.createAccessor().setType('VEC3').setArray(new Float32Array([0,0,1, 0,0,1, 0,0,1])).setBuffer(b);
  const indices = d.createAccessor().setType('SCALAR').setArray(new Uint16Array([0,1,2])).setBuffer(b);
  for (let i = 0; i < groups; i++) {
    const material = d.createMaterial('Material '+i).setBaseColorFactor([distinct ? i/groups : .6,.2,.3,1]);
    const p = d.createPrimitive().setAttribute('POSITION', position).setAttribute('NORMAL',normal).setAttribute('TEXCOORD_0',uv).setIndices(indices).setMaterial(material);
    const mesh = d.createMesh().addPrimitive(p);
    scene.addChild(d.createNode().setMesh(mesh).setTranslation([i*2,0,0]).setScale([i===1?-1:1,1,1]));
  }
  return io.writeBinary(d);
}
async function modify(bytes, fn) {
  const {json,resources} = await io.binaryToJSON(bytes); fn(json);
  // A glTF ZIP lets tests encode deliberately invalid JSON without the writer
  // normalizing it or rejecting it first.
  const data = {...resources};
  for (const [i,b] of (json.buffers || []).entries()) {data[`buffer-${i}.bin`] = resources[b.uri || '@glb.bin']; b.uri = `buffer-${i}.bin`;}
  delete data['@glb.bin'];
  data['scene.gltf'] = new TextEncoder().encode(JSON.stringify(json));
  return zipSync(data);
}
test('merges equivalent materials and preserves transforms, mirror winding and metadata', async () => {
  const bytes = await fixture(), copy = bytes.slice();
  const result = await optimize(bytes,'glb');
  assert.deepEqual(bytes,copy);
  assert.equal(result.report.before.groups,2); assert.equal(result.report.after.groups,1);
  assert.equal(result.report.after.triangles,2); assert.equal(result.report.compatible,true);
  const {doc,asset} = await readModel(result.bytes,'glb');
  assert.deepEqual(asset.extras,{author:'Example creator',license:'CC BY 4.0'});
  const p = doc.getRoot().listMeshes()[0].listPrimitives()[0];
  const a=[],b=[],c=[]; const pos=p.getAttribute('POSITION'), idx=p.getIndices();
  for(let i=0;i<idx.getCount();i+=3) {
    pos.getElement(idx.getScalar(i),a);pos.getElement(idx.getScalar(i+1),b);pos.getElement(idx.getScalar(i+2),c);
    assert.ok((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]) > 0,'mirrored winding is corrected');
  }
  assert.equal(Math.max(...pos.getArray()),2);
  assert.deepEqual((await optimize(bytes,'glb')).bytes,result.bytes,'deterministic output');
});
test('keeps distinct materials and explains the importer group limit', async () => {
  const r=await optimize(await fixture(17,true),'glb');
  assert.equal(r.report.after.groups,17);assert.equal(r.report.compatible,false);
  assert.match(r.report.issues[0],/texture atlas/);
});
test('glTF ZIP and GLB produce the same geometry',async()=>{
  const source=await fixture();const r=await optimize(await modify(source,()=>{}),'zip');
  assert.deepEqual(r.report.after,(await optimize(source,'glb')).report.after);
});
test('rejects unsupported inputs before reduction',async()=>{
  const source=await fixture();
  const cases=[
    [j=>j.animations=[{}],/static models/],
    [j=>j.extensionsUsed=['KHR_draco_mesh_compression'],/ordinary glTF/],
    [j=>j.materials[0].alphaMode='BLEND',/transparency/],
    [j=>j.materials[0].normalTexture={index:0},/Bake lighting/],
    [j=>j.meshes[0].primitives[0].attributes.COLOR_0=0,/vertex colors/],
    [j=>delete j.meshes[0].primitives[0].attributes.TEXCOORD_0,/UV coordinates/],
    [j=>j.meshes[0].primitives[0].targets=[{}],/morph targets/],
    [j=>j.nodes[0].children=[0],/cyclic/],
    [j=>j.accessors[0].count=1e9,/attributes exceed/],
  ];
  for(const [mutate,error] of cases) await assert.rejects(()=>modify(source,mutate).then(b=>optimize(b,'zip')),error);
  await assert.rejects(()=>optimize(new Uint8Array([1,2,3]),'glb'),/binary GLB/);
  await assert.rejects(()=>optimize(new Uint8Array(LIMITS.fileBytes+1),'glb'),/64 MiB/);
  for(const triangles of [0,99,200001,100.5,'20000']) await assert.rejects(()=>optimize(source,'glb',{triangles}),/target/);
  await assert.rejects(()=>optimize(source,'glb',{error:.5}),/protection/);
});
test('rejects remote paths, traversal, missing files, duplicate paths and ZIP entry limits',async()=>{
  const source=await fixture(), document=await io.binaryToJSON(source);
  const make=j=>zipSync({'scene.gltf':new TextEncoder().encode(JSON.stringify(j))});
  for(const uri of ['https://example.com/model.bin','../model.bin','missing.bin']) {
    const j=structuredClone(document.json);j.buffers[0].uri=uri;
    await assert.rejects(()=>optimize(make(j),'zip'),/relative paths|Missing model/);
  }
  await assert.rejects(()=>optimize(zipSync({'a':new Uint8Array(),'A':new Uint8Array()}),'zip'),/conflicting/);
  await assert.rejects(()=>optimize(zipSync(Object.fromEntries(Array.from({length:257},(_,i)=>['f'+i,new Uint8Array()]))),'zip'),/256/);
});
test('bundled worker agrees with native JS and uses no general eval', async()=>{
  const url=new URL('../../package_creation/service/static/vendor/object-optimizer.js',import.meta.url);
  const code=await fs.readFile(url,'utf8');assert.doesNotMatch(code,/\bnew Function\b|\beval\(/);
  const w=new Worker(`const {parentPort}=require('node:worker_threads');globalThis.postMessage=x=>parentPort.postMessage(x);import(${JSON.stringify(url.href)}).then(()=>parentPort.on('message',data=>globalThis.onmessage({data})));`,{eval:true});
  try {
    const source=await fixture(), result=new Promise((resolve,reject)=>{
      w.on('error',reject);w.on('message',r=>{if(r.error)reject(new Error(r.error.message));else if(r.result)resolve(r);});
    });
    w.postMessage({version:1,id:7,job:'test',revision:2,bytes:source.buffer,format:'glb',options:{}});
    const r=await result;assert.equal(r.id,7);assert.equal(r.job,'test');assert.equal(r.revision,2);
    assert.deepEqual(r.result.bytes,(await optimize(source,'glb')).bytes);
  } finally {await w.terminate();}
});

const realSource=process.env.OBJECT_OPTIMIZER_MODEL;
test('real model preserves image bytes and credits while meeting importer budgets',{skip:!realSource},async()=>{
  const bytes=new Uint8Array(await fs.readFile(realSource));
  const before=await readModel(bytes,'glb'), result=await optimize(bytes,'glb'), after=await readModel(result.bytes,'glb');
  const images=doc=>doc.getRoot().listTextures().map(t=>Buffer.from(t.getImage()).toString('base64')).sort();
  assert.deepEqual(images(after.doc),images(before.doc));
  assert.deepEqual(after.asset.extras,before.asset.extras);
  assert.ok(result.report.after.triangles<30000);assert.ok(result.report.after.groups<=16);
  assert.equal(result.report.compatible,true);
  assert.deepEqual((await optimize(bytes,'glb')).bytes,result.bytes);
});
