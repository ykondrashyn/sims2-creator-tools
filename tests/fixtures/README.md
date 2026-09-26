# Reference fixtures

Synthetic verification uses generated images, empty DBPF containers and small
model archives. It needs no private game content. `registry.json` pins the local
reference manifests and tattoo scaffolds used by full verification. The manifests
in turn identify game resources, recipes, source hashes and creator provenance.

Prepare the reference data with the existing offline extraction tools:

- Tattoos: `package_creation/README.md` and `package_creation/templates`.
- Hair and Rose 72: `package_creation/hair/README.md`, its installed manifests
  and the original mesh/recolor bundle. Never substitute a different hairstyle.
- Objects: `scripts/build_object_references.mjs` and the object asset catalog.
- Paintings: the painting catalog and its verified artwork recipes.
- Sims: `scripts/extract_sim_references.py`, local scaffolds and the recorded
  fixture provenance. Keep the experimental export gate.
- Body conversion: `scripts/build_conversion_assets.py` and
  `package_creation/tests/conversion_reference.py`, using pinned Blender 3.4.1
  only for offline references. Compare all decoded RGBA bytes.

Supply a matching fixture tree with `--fixture-root` or `PROJECT_FIXTURE_ROOT`.
A missing or changed reference is an error for the selected full profile. Do
not lower acceptance thresholds or silently use an older build to bypass it.

Historical engine fixtures must include their original manifest and all assets
it references. They are used only by explicitly named compatibility tests.
Do not install test packages into the normal game profile.
