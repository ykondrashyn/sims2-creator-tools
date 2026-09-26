import { WebIO, Logger, PropertyType } from '@gltf-transform/core';
import { dedup, flatten, join, weld, compactPrimitive, prune, unpartition } from '@gltf-transform/functions';
import { MeshoptSimplifier } from 'meshoptimizer/simplifier';
import { unzipSync } from 'fflate';

export const VERSION = 1;
export const LIMITS = Object.freeze({fileBytes: 64 * 1024 ** 2, expandedBytes: 128 * 1024 ** 2,
  vertices: 2_000_000, triangles: 1_000_000, groups: 4096, nodes: 8192});
const require = (condition, message) => { if (!condition) throw new Error(message); };
const integer = n => Number.isSafeInteger(n) && n >= 0;
const safePath = p => typeof p === 'string' && p.length && !p.startsWith('/') &&
  !/[\\:%]/.test(p) && p.split('/').every(v => v && v !== '.' && v !== '..');
const io = () => new WebIO().setLogger(new Logger(Logger.Verbosity.SILENT));

/** No URI-reading method is used. All resources must be supplied in memory. */
export async function readModel(bytes, format) {
  require(bytes.byteLength <= LIMITS.fileBytes, 'Model file exceeds 64 MiB. Export a smaller source model.');
  let source;
  if (format === 'zip') {
    let expanded = 0, entries = 0;
    const names = new Set();
    const files = unzipSync(bytes, {filter: entry => {
      require(++entries <= 256, 'Model ZIP has more than 256 entries.');
      const name = entry.name.replace(/\/$/, '');
      require(safePath(name), 'Model ZIP paths must stay inside the archive.');
      require(!names.has(name.toLowerCase()), 'Model ZIP has conflicting filenames.');
      names.add(name.toLowerCase());
      require(entry.originalSize <= LIMITS.fileBytes, 'Model ZIP entry exceeds 64 MiB.');
      expanded += entry.originalSize;
      require(expanded <= LIMITS.expandedBytes, 'Expanded model ZIP exceeds 128 MiB.');
      return !entry.name.endsWith('/');
    }});
    require(Object.values(files).reduce((n, b) => n + b.length, 0) <= LIMITS.expandedBytes,
      'Expanded model ZIP exceeds 128 MiB.');
    const scenes = Object.keys(files).filter(n => n.toLowerCase().endsWith('.gltf'));
    require(scenes.length === 1, 'The ZIP must contain exactly one .gltf scene.');
    const name = scenes[0], base = name.slice(0, name.lastIndexOf('/') + 1);
    source = {json: JSON.parse(new TextDecoder().decode(files[name])), resources: Object.create(null)};
    for (const item of [...(source.json.buffers || []), ...(source.json.images || [])]) {
      if (!item.uri || item.uri.startsWith('data:')) continue;
      require(safePath(item.uri), 'Model references must be relative paths inside the ZIP. External URLs are unsupported.');
      require(files[base + item.uri], `Missing model file ${item.uri}. Include it inside the ZIP.`);
      source.resources[item.uri] = files[base + item.uri];
    }
  } else {
    require(format === 'glb' && new TextDecoder().decode(bytes.subarray(0, 4)) === 'glTF',
      'Choose a binary GLB or a ZIP containing one glTF scene.');
    source = await io().binaryToJSON(bytes);
  }
  validateJSON(source.json);
  const doc = await io().readJSON(source);
  const root = doc.getRoot(), scene = root.getDefaultScene() || root.listScenes()[0];
  // Match the package importer, which uses the default scene or the first scene.
  for (const other of root.listScenes()) if (other !== scene) other.dispose();
  root.setDefaultScene(scene);
  for (const node of root.listNodes()) {
    const m = node.getWorldMatrix();
    const determinant = m[0]*(m[5]*m[10]-m[9]*m[6]) - m[4]*(m[1]*m[10]-m[9]*m[2]) + m[8]*(m[1]*m[6]-m[5]*m[2]);
    require(m.every(Number.isFinite) && Math.abs(determinant) > 1e-15,
      'Model transform is invalid or has zero scale. Apply a nonzero scale before exporting.');
  }
  for (const mesh of root.listMeshes()) for (const p of mesh.listPrimitives()) {
    const pos = p.getAttribute('POSITION');
    for (const attr of p.listAttributes()) {
      require(attr.getCount() === pos.getCount() && attr.getArray().every(Number.isFinite),
        'Model vertex attributes are inconsistent or contain invalid numbers.');
    }
    require(pos.getArray().every(v => Math.abs(v) < 1e6), 'Model coordinates are too large. Apply its scale before exporting.');
    const indices = p.getIndices();
    require(!indices || indices.getArray().every(v => integer(v) && v < pos.getCount()), 'Model contains invalid triangle indices.');
  }
  // No image decoding or re-encoding. Image bytes and alpha are kept as supplied.
  for (const texture of root.listTextures()) {
    const size = texture.getSize();
    require(['image/png', 'image/jpeg'].includes(texture.getMimeType()) && size?.every(v => v > 0 && v <= 2048),
      'Use PNG or JPEG base-color textures up to 2048 pixels per side. Resize larger images before exporting.');
  }
  return {doc, asset: structuredClone(source.json.asset)};
}

function validateJSON(j) {
  require(j.asset?.version === '2.0', 'Use a glTF 2 model.');
  require(!j.animations?.length && !j.skins?.length, 'Optimization supports static models. Export a posed model without animations or skinning.');
  require(!j.extensionsRequired?.length && !j.extensionsUsed?.length,
    'Export an ordinary glTF 2 model without compression or material extensions before optimizing.');
  require(j.nodes?.length <= LIMITS.nodes && j.scenes?.length, 'Model has no scene or too many scene nodes.');
  let allocated = 0;
  for (const b of j.buffers || []) {
    require(integer(b.byteLength), 'Model buffer length is invalid.');
    allocated += b.byteLength;
  }
  require(allocated <= LIMITS.expandedBytes, 'Model buffers exceed 128 MiB.');
  allocated = 0;
  for (const a of j.accessors || []) {
    const components = {SCALAR:1,VEC2:2,VEC3:3,VEC4:4,MAT2:4,MAT3:9,MAT4:16}[a.type];
    require(integer(a.count) && components && [5120,5121,5122,5123,5125,5126].includes(a.componentType), 'Model accessor is invalid.');
    allocated += a.count * components * 4;
    require(allocated <= LIMITS.expandedBytes, 'Decoded model attributes exceed 128 MiB.');
  }
  for (const material of j.materials || []) {
    const pbr = material.pbrMetallicRoughness || {};
    require(material.alphaMode !== 'BLEND', 'Blended transparency is unsupported. Export opaque or alpha-cutout materials.');
    require(!material.normalTexture && !material.occlusionTexture && !material.emissiveTexture &&
      !pbr.metallicRoughnessTexture && !(material.emissiveFactor || []).some(v => v !== 0),
      'Bake lighting, normal and other material maps into base-color textures before optimizing.');
    require(!pbr.baseColorTexture?.texCoord, 'Base-color textures must use UV0.');
  }
  const scene = j.scenes[j.scene ?? 0];
  require(scene, 'Default model scene is missing.');
  const seen = new Set();
  let vertices = 0, triangles = 0, groups = 0;
  function visit(id, depth) {
    require(depth < 64 && !seen.has(id) && j.nodes[id], 'Model scene is cyclic or repeats a node.');
    seen.add(id);
    const node = j.nodes[id];
    require(node.skin === undefined, 'Use a static model without skinning.');
    if (node.mesh !== undefined) {
      require(j.meshes?.[node.mesh], 'Model mesh is missing.');
      for (const p of j.meshes[node.mesh].primitives) {
        require((p.mode ?? 4) === 4 && !p.targets?.length, 'Use triangle meshes without morph targets.');
        require(Object.keys(p.attributes || {}).every(k => ['POSITION','NORMAL','TEXCOORD_0','TEXCOORD_1','TANGENT'].includes(k)),
          'Bake vertex colors into a base-color texture and remove rigging before optimizing.');
        const pos = j.accessors[p.attributes?.POSITION], uv = j.accessors[p.attributes?.TEXCOORD_0];
        require(pos?.type === 'VEC3' && pos.componentType === 5126 && pos.count >= 3 && uv?.type === 'VEC2',
          'Every model group needs positions and UV coordinates (TEXCOORD_0).');
        const count = p.indices === undefined ? pos.count : j.accessors[p.indices]?.count;
        require(count > 0 && count % 3 === 0, 'Model has invalid triangle indices.');
        vertices += pos.count; triangles += count / 3; groups++;
        require(vertices <= LIMITS.vertices && triangles <= LIMITS.triangles && groups <= LIMITS.groups,
          'Optimization supports up to 1 million triangles, 2 million vertices and 4096 source groups. Reduce the source in a 3D editor.');
      }
    }
    for (const child of node.children || []) visit(child, depth + 1);
  }
  for (const id of scene.nodes || []) visit(id, 0);
  require(groups > 0, 'The selected scene has no model geometry.');
}

function stats(doc, bytes) {
  const s = {triangles:0, vertices:0, groups:0, materials:0, bytes};
  const materials = new Set();
  doc.getRoot().getDefaultScene().traverse(node => {
    for (const p of node.getMesh()?.listPrimitives() || []) {
      const count = p.getAttribute('POSITION').getCount();
      s.vertices += count; s.triangles += (p.getIndices()?.getCount() ?? count) / 3; s.groups++;
      materials.add(p.getMaterial());
    }
  });
  s.materials = materials.size;
  return s;
}

export async function optimize(bytes, format, options = {}, progress = () => {}) {
  const started = performance.now();
  const target = options.triangles ?? 20000, error = options.error ?? 0.01;
  require(integer(target) && target >= 100 && target <= 200000, 'Choose a target of 100 to 200000 triangles.');
  require([0.002,0.01,0.03].includes(error), 'Choose High, Balanced or Low detail protection.');
  progress('Reading and checking the source model…');
  const {doc, asset} = await readModel(bytes, format), before = stats(doc, bytes.byteLength);
  await MeshoptSimplifier.ready;
  progress('Merging equivalent materials and compatible geometry…');
  await doc.transform(dedup({propertyTypes:[PropertyType.TEXTURE,PropertyType.MATERIAL]}), flatten(), join(), weld());
  const primitives = doc.getRoot().listMeshes().flatMap(m => m.listPrimitives());
  const total = primitives.reduce((n, p) => n + p.getIndices().getCount() / 3, 0);
  const ratio = Math.min(1, target / total);
  let maxError = 0;
  for (const [index, p] of primitives.entries()) {
    progress(`Reducing geometry ${index + 1} of ${primitives.length}…`);
    const positions = p.getAttribute('POSITION').getArray();
    const count = positions.length / 3, normal = p.getAttribute('NORMAL'), uv = p.getAttribute('TEXCOORD_0');
    const indices = new Uint32Array(p.getIndices().getArray());
    if (ratio < 1 && indices.length > 300) {
      const attributes = new Float32Array(count * 5), n = [], t = [];
      for (let v = 0; v < count; v++) {
        if (normal) {normal.getElement(v, n); attributes.set(n, v * 5);}
        uv.getElement(v, t); attributes.set(t, v * 5 + 3);
      }
      const remap = MeshoptSimplifier.generatePositionRemap(positions, 3), locks = new Uint8Array(count);
      for (let v = 0; v < count; v++) {
        const r = remap[v], a = v * 5, b = r * 5;
        const seam = Math.abs(attributes[a+3] - attributes[b+3]) > 1e-6 || Math.abs(attributes[a+4] - attributes[b+4]) > 1e-6;
        const sharp = normal && attributes[a]*attributes[b] + attributes[a+1]*attributes[b+1] + attributes[a+2]*attributes[b+2] < Math.SQRT1_2;
        // meshopt_SimplifyVertex_Protect preserves discontinuities while still
        // allowing the connected surface to simplify. LockBorder protects joins
        // between distinct material groups.
        if (r !== v && (seam || sharp)) {locks[v] |= 2; locks[r] |= 2;}
      }
      // Attribute-aware reduction can simplify faceted meshes. Reuses source
      // positions/UVs/normals, with bounded error. Does not prune small components.
      const goal = Math.max(300, Math.floor(indices.length * ratio / 3) * 3);
      const [reduced, reached] = MeshoptSimplifier.simplifyWithAttributes(indices, positions, 3,
        attributes, 5, [.5,.5,.5,1,1], locks, goal, error, ['Permissive','LockBorder']);
      maxError = Math.max(maxError, reached);
      p.setIndices(p.getIndices().clone().setArray(reduced));
      compactPrimitive(p);
    }
  }
  await doc.transform(prune({keepAttributes:true,keepIndices:true,keepExtras:true,keepSolidTextures:true}), unpartition());
  Object.assign(doc.getRoot().getAsset(), asset);
  doc.getRoot().getAsset().generator = 'Sims 2 Creator Tools optimizer v1';
  progress('Checking the optimized model…');
  const output = await io().writeBinary(doc), after = stats(doc, output.byteLength);
  const issues = [];
  if (after.groups > 16) issues.push(`${after.groups} material groups remain. The importer accepts 16. Combine distinct materials into a texture atlas in a 3D editor.`);
  if (primitives.some(p => p.getAttribute('POSITION').getCount() > 65535))
    issues.push('A group still exceeds 65535 vertices. Lower the target or detail protection and try again.');
  if (primitives.some(p => p.getIndices().getCount() > 600000)) issues.push('A group still exceeds 200000 triangles. Lower the triangle target.');
  if (output.byteLength > LIMITS.fileBytes) issues.push('Optimized GLB exceeds 64 MiB. Reduce the source texture sizes.');
  let decoded = 0;
  for (const p of primitives) {
    const size = p.getMaterial()?.getBaseColorTexture()?.getSize() || [4,4];
    decoded += size[0] * size[1] * 4;
  }
  if (decoded > LIMITS.expandedBytes) issues.push('Material textures exceed 128 MiB when decoded. Reduce their dimensions.');
  const report = {version:VERSION, before, after, target, error, maxError, compatible:!issues.length, issues,
    targetReached:after.triangles <= target, milliseconds:Math.round(performance.now() - started)};
  return {bytes:output, report};
}
