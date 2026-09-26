# TS2 body preview assets

Three.js 0.185.1, including GLTFLoader and OrbitControls, is bundled locally with esbuild 0.28.2. No CDN, import map or runtime npm installation is needed. Three.js is MIT licensed, with its full license in `package_creation/service/static/vendor/THREE-LICENSE.txt`. `@napi-rs/canvas` is used only by development tests.

To reproduce the bundle and run texture tests:

```sh
cd tools/body-preview
npm ci --ignore-scripts
npm run build
npm test
```

Only the selected `ts2 body` object from the repository's existing AM/AF Tattooer templates is exported. Both world transforms are retained, giving upright glTF bodies facing +Z. GLB UVs account for glTF's top-left image origin, and CanvasTexture uses `flipY = false`. The generated assets contain geometry, normals and UVs, without images, materials, animations, external resources or morphs. The existing model/template attribution in the root README continues to apply.

To regenerate and compare every exported triangle and UV against its source:

```sh
/path/to/blender-3.4.1 --background --python scripts/export_preview_bodies.py
/path/to/blender-3.4.1 --background --python scripts/validate_preview_bodies.py
.venv/bin/python -m unittest package_creation.tests.test_service -q
```

Run these commands from the repository root. `package_creation/service/preview_assets/manifest.json` records pinned template hashes, output hashes, vertex counts and triangle counts. The scripts never save the `.blend` files. AM has 1,768 triangles and AF has 1,930. Export and validation require the original pinned templates, while serving the website needs only the prebuilt GLBs and scripts.

The JavaScript tests check actual canvas alpha blending, layer order independent of menu order, missing genders, input limits, old-pixel removal and out-of-order asynchronous decoding. Service tests cover trusted LAN model access, static allowlists and existing UI/API behavior. Browser checks should cover both genders, conversion results, visibility, camera controls, resizing and console errors. These checks do not establish Sims 2 gameplay behavior.
