# Third-party notices

## chieltbest/dbpf

The Rust helper links to [chieltbest/dbpf](https://github.com/chieltbest/dbpf) at commit `99b167f80781f7a3862dcd1edf9ce6ca169ac087`.

That project identifies itself as GPL-3.0-or-later. The linked Rust helper in `builder/` is therefore marked GPL-3.0-or-later. This harness is currently a private prototype and has not been prepared for binary distribution.

## Pick'N'Mix templates

`prepare` downloads [Multi Overlays With Box V1](https://www.picknmixmods.com/Sims2/Notes/ScriptedOverlayBoxes/ScriptedOverlayBoxes.html) from the URL and SHA-256 recorded in `templates/manifest.json`.

The downloaded archive and its three package templates remain ignored local inputs and are not committed by this workspace. Private generated packages combine transformed template resources and the `NoFaceOverlay.package` texture into one DBPF container. The original template package, object behavior, box graphic, and NoFaceOverlay package are the work of their respective creators as credited by the archive.

The current implementation is restricted to private owner-operated use. The upstream redistribution terms for the Pick'N'Mix template and `NoFaceOverlay.package` have not been established strongly enough for a public hosted service. Resolve those terms before sharing generated packages beyond the private LAN.

## Historical design reference

The package relationship follows the Sims 2 body-overlay mechanism documented by Morague's [Body Overlay Tattoo Tutorial](https://modthesims.info/t/239882) and the later Pick'N'Mix scripted-overlay templates. No SimPE code is included or invoked.
