# Browser package runtime

The six creator tools share the Rust engine through worker protocol version 1.
Inputs, package creation and downloads remain local. IndexedDB retains jobs,
immutable snapshots, identities, checkpoints, output Blobs and pinned assets.
Existing serialization and experimental Sim restrictions are unchanged.

The static edition obtains its versioned manifest from an HTML meta tag. Engine,
worker, image, preview and template URLs are relative content-addressed files.
There are no runtime API calls. See [architecture](../../docs/architecture.md) and
[deployment](../../docs/deployment.md) for build and verification instructions.

The corresponding source archive contains the engine, dependency sources and
licenses. Raw game installations, user files and raw GIMP curves are excluded.
The built-in normalized Pooklet mappings are distributed as manifest assets.
