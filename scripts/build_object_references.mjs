// Offline, deterministic reference assets. Keep source meshes at their authored scale.
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import * as THREE from '../tools/body-preview/node_modules/three/build/three.module.js';
import {GLTFLoader} from '../tools/body-preview/node_modules/three/examples/jsm/loaders/GLTFLoader.js';
const reports = path.resolve(process.argv[2] || 'artifacts/object-sizing/templates');
const out = new URL('../package_creation/objects/assets/', import.meta.url);
const hash = b => createHash('sha256').update(b).digest('hex');
const bodies = {};
for (const gender of ['am', 'af']) {
  const bytes = await fs.readFile(new URL(`../package_creation/service/preview_assets/${gender}.glb`, import.meta.url));
  const {scene} = await new GLTFLoader().parseAsync(Uint8Array.from(bytes).buffer, '');
  const b = new THREE.Box3().setFromObject(scene);
  const bodyHeight = b.max.y - b.min.y;
  bodies[gender] = {asset:`object-reference-${gender}`, sha256:hash(bytes), floor_offset:-b.min.y,
    body_height:bodyHeight, head:{radii:[0.092,0.13,0.105], center:[0,bodyHeight+0.095,-0.055]},
    assembled_height:bodyHeight+0.225};
  await fs.writeFile(new URL(`reference-${gender}.glb`, out), bytes);
}
// Native graph extraction records translations from the pinned table CRES.
const graph = JSON.parse(await fs.readFile(path.join(reports,'dining-table-scene-graph.json')));
const table = JSON.parse(await fs.readFile(path.join(reports,'dining-table-clone.json')));
const catalog = JSON.parse(await fs.readFile(new URL('catalog.json', out)));
const t = catalog.items.find(t=>t.id==='dining-table');
const surface = graph.scenes[0].transforms.find(t=>Math.abs(t[0]-1)<1e-6 && t[1]===0 && t[2]>0)[2];
if (Math.abs(surface-0.8)>1e-6) throw new Error('The verified table surface changed');
const tableBytes = Buffer.from(table.original.meshes[0].glb, 'base64');
await fs.writeFile(new URL('reference-table.glb', out), tableBytes);
const reference = {version:1,sizing_version:2,units:'Sims 2 game units, one floor tile = 1 unit',
  reference_height:Math.fround(bodies.am.assembled_height),
  definition:'Canonical AM source body, floor aligned, plus a neutral proportional head. This is a scale reference, not a complete Sim.',
  bodies,table:{asset:'object-reference-table',surface_height:surface,
    source_label:t.label,source_package_sha256:t.sha256,
    source_cres:t.resources.find(r=>r.key.startsWith('e519c933-')),sha256:hash(tableBytes)}};
await fs.writeFile(new URL('object-reference.json', out), JSON.stringify(reference,null,2)+'\n');
console.log(JSON.stringify(reference));
