/** Verify lossless, deterministic archive packing without rebuilding packages. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const root=path.resolve(process.argv[2] || 'artifacts/archive-packing');
await fs.mkdir(root,{recursive:true});
const load=async p=>import('data:text/javascript;base64,'+(await fs.readFile(p)).toString('base64'));
const {archive,crc32}=await load('package_creation/service/static/vendor/package-archive.js');
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
function adapter({fail,corrupt,cancelAt=Infinity}={}) {
  const blobs=new Map();
  let checks=0,peakChunk=0;
  return {blobs,metrics:()=>({peakChunk}),
    checkActive(){if(++checks===cancelAt)throw Error('cancelled');},
    async getBlob(k){return blobs.get(k);},
    async putBlob(k,b){
      peakChunk=Math.max(peakChunk,b.size);
      assert(b.size<=4*1024**2,'bounded storage chunk');
      if(fail?.(k))throw Error('quota exhausted');
      if(corrupt?.(k)){const bytes=new Uint8Array(await b.arrayBuffer());bytes[0]^=1;b=new Blob([bytes]);}
      blobs.set(k,b);
    },
    async deleteBlob(k){blobs.delete(k);},
  };
}
async function pack(entries,token='attempt',io=adapter(),engine=archive){
  const report=await engine({id:'test'},entries,token,io);
  const blob=new Blob(await Promise.all(report.parts.map(k=>io.getBlob(k))));
  assert.equal(blob.size,report.size);
  assert(![...io.blobs.keys()].some(k=>k.includes(':deflate:')),'no temporary compressed data remains');
  return {bytes:Buffer.from(await blob.arrayBuffer()),report,metrics:io.metrics()};
}
let seed=123456789;
const random=n=>Uint8Array.from({length:n},()=>{seed^=seed<<13;seed^=seed>>>17;seed^=seed<<5;return seed&255;});
const fixtures=[
  {name:'README.txt',blob:new Blob(['A lossless archive.\n'.repeat(1000)])},
  {name:'Empty.txt',blob:new Blob([])},
  {name:'tiny.bin',blob:new Blob([new Uint8Array([1,2,3])])},
  {name:'Meshes/Multichunk.package',blob:new Blob([random(4*1024**2+70000)])},
  {name:'Unicode_é.package',blob:new Blob([new Uint8Array(9*1024**2).fill(127)])},
];
const first=await pack(fixtures),retry=await pack(fixtures,'retry');
assert.deepEqual(first.bytes,retry.bytes);
assert(first.report.compression.compressed_members>=2);
assert(first.report.compression.stored_members>=2);
assert(first.report.compression.stored_member_bytes<first.report.compression.original_member_bytes);
assert.equal(crc32(new TextEncoder().encode('123456789')),0xcbf43926);
const checks=['mixed stored/deflated members, empty/short/random/repeated data, Unicode paths, multiple storage chunks, deterministic retries'];
async function rejected(entries,io,pattern){
  await assert.rejects(()=>pack(entries,'failure',io),pattern);
  assert(![...io.blobs.keys()].some(k=>k.includes(':deflate:')));
}
await rejected(fixtures,adapter({cancelAt:3}),/cancelled/);
await rejected(fixtures,adapter({fail:k=>k.includes(':deflate:')}),/quota/);
await rejected(fixtures,adapter({corrupt:k=>k.includes(':deflate:')}),/CRC/);
await rejected(fixtures,adapter({corrupt:k=>!k.includes(':deflate:')}),/CRC/);
await rejected([{name:'lost.package',key:'absent'}],adapter(),/missing/);
await rejected([fixtures[0],{...fixtures[0],name:'readme.TXT'}],adapter(),/collide/);
checks.push('cancellation, quota failure, corrupt temporary/final data, missing input and filename collision');
const results=[];
async function verify(name,entries,packed){
  const zip=path.join(root,name+'.zip');await fs.writeFile(zip,packed.bytes);
  const expected={};for(const e of entries)expected[e.name]=hash(Buffer.from(await e.blob.arrayBuffer()));
  const metadata=path.join(root,name+'-expected.json');await fs.writeFile(metadata,JSON.stringify(expected));
  const actual=JSON.parse(execFileSync('.venv/bin/python',['-c',
    'import zipfile,hashlib,json,sys\nz=zipfile.ZipFile(sys.argv[1])\nassert z.testzip() is None\na={i.filename:hashlib.sha256(z.read(i)).hexdigest() for i in z.infolist()}\nassert a==json.load(open(sys.argv[2]))\nprint(json.dumps({"members":len(a),"methods":{i.filename:i.compress_type for i in z.infolist()}}))',zip,metadata],{encoding:'utf8'}));
  results.push({name,bytes:packed.bytes.length,sha256:hash(packed.bytes),...packed.report.compression,metrics:packed.metrics,...actual});
}
await verify('synthetic',fixtures,first);
const old=await load(new URL('./fixtures/zip-stored.mjs',import.meta.url));
const prior=await pack(fixtures,'old',adapter(),old.archive);
await verify('historical-stored',fixtures,prior);
assert(results.at(-1).methods['README.txt']===0);
checks.push('previous pinned archive implementation still runs unchanged');
const source='artifacts/texture-encoders/final/directxtex/native-parity';
for(const count of [4,15]) {
  const names=(await fs.readdir(source)).filter(p=>p.startsWith('Rose_')&&!p.startsWith('Rose_custom_')&&p.endsWith('.package')).sort();
  const selected=count===4?['Rose_Dynamite.package','Rose_DepthCharge.package','Rose_Incendiary.package','Rose_Explosive.package']:names;
  assert.equal(selected.length,count);
  const entries=await Promise.all(selected.map(async name=>({name,blob:new Blob([await fs.readFile(path.join(source,name))])})));
  entries.push({name:'Meshes/mesh_rosehair_0124.package',blob:new Blob([await fs.readFile('artifacts/hair-size-analysis/mesh_rosehair_0124.package')])});
  const readme=execFileSync('.venv/bin/python',['-c',
    'import zipfile,sys\nsys.stdout.buffer.write(zipfile.ZipFile(sys.argv[1]).read("README.txt"))',
    'artifacts/texture-encoders/firefox-final-hair/downloads/BrowserTest_Rose_Recolors.zip']);
  entries.push({name:'README.txt',blob:new Blob([readme])});
  const before=await pack(entries,'old',adapter(),old.archive);
  const began=performance.now(),packed=await pack(entries);
  await verify('rose-'+count,entries,packed);
  Object.assign(results.at(-1),{stored_archive_bytes:before.bytes.length,saved_bytes:before.bytes.length-packed.bytes.length,
    saving_percent:100*(1-packed.bytes.length/before.bytes.length),milliseconds:performance.now()-began});
}
await fs.writeFile(path.join(root,'results.json'),JSON.stringify({checks,results},null,2)+'\n');
console.log(JSON.stringify({checks,results:results.map(({methods,...r})=>r)},null,2));
