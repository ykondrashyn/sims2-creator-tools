"""Build the pinned browser engine and its immutable assets for the trusted LAN."""

from __future__ import annotations
import argparse
import hashlib
import gzip
import io
import json
import os
import posixpath
import shutil
import sys
import subprocess
import tarfile
import tempfile
from tools.archive import deterministic_tar
from tools.project import environment, source_identity
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "package_creation"
FIXTURES = Path(os.environ.get("PROJECT_FIXTURE_ROOT", ROOT)) / "package_creation"
OUT = PACKAGE / "service/package_assets"


def digest(data):
    return hashlib.sha256(data).hexdigest()


COMPRESSIBLE = {
    "application/wasm",
    "text/javascript",
    "application/javascript",
    "text/css",
    "text/html",
    "application/json",
}


def precompress(path: Path, metadata):
    """Publish an additive gzip representation without changing decoded identity."""
    if metadata["mime"] not in COMPRESSIBLE:
        return
    data = path.read_bytes()
    output = io.BytesIO()
    # GzipFile fixes the OS byte and omits filenames across supported Python hosts.
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, compresslevel=6, mtime=0) as gz:
        gz.write(data)
    compressed = output.getvalue()
    if len(compressed) * 100 > len(data) * 95:
        return
    path.with_name(path.name + ".gz").write_bytes(compressed)
    metadata["encodings"] = {"gzip": {"sha256": digest(compressed), "size": len(compressed)}}


def file_metadata(path: Path, mime: str):
    metadata = {"sha256": digest(path.read_bytes()), "size": path.stat().st_size, "mime": mime}
    precompress(path, metadata)
    return metadata


def write_delivery(root: Path, manifest):
    """The manifest cannot contain its own encoded digest, so keep its envelope separate."""
    path = root / "manifest.json"
    temporary = root / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2) + "\n")
    temporary.replace(path)
    metadata = file_metadata(path, "application/json")
    (root / "manifest-http.json").write_text(json.dumps(metadata, indent=2) + "\n")


def main():
    global OUT
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-compile",
        action="store_true",
        help="Development only, never accepted by build-release",
    )
    parser.add_argument("--target-dir", type=Path, default=ROOT / "artifacts/build/target")
    parser.add_argument("--output", type=Path, default=OUT, help="Runtime staging directory")
    parser.add_argument(
        "--source-static-root",
        type=Path,
        help="Staged static files to include in corresponding source",
    )
    parser.add_argument(
        "--source",
        action="store_true",
        default=True,
        help="Corresponding source is always included with the runtime",
    )
    args = parser.parse_args()
    BUILD_ENV = environment()
    TOOLCHAIN = Path(
        BUILD_ENV.get("PACKAGE_RUST_BIN")
        or Path(shutil.which("cargo", path=BUILD_ENV["PATH"])).parent
    )
    WASM_BINDGEN = Path(
        os.environ.get("PACKAGE_WASM_BINDGEN", PACKAGE / ".tools/wasm/bin/wasm-bindgen")
    )
    OUT = args.output
    env = {
        **BUILD_ENV,
        "PATH": str(TOOLCHAIN) + os.pathsep + os.environ["PATH"],
        "CARGO_HOME": BUILD_ENV.get("CARGO_HOME", str(Path.home() / ".cargo")),
        "RUSTFLAGS": BUILD_ENV.get("RUSTFLAGS", "") + " -C link-arg=--max-memory=1073741824",
    }
    texture_vendor = PACKAGE / "builder/vendor/directxtex"
    texture_provenance = json.loads((texture_vendor / "provenance.json").read_text())
    for name, expected in texture_provenance["files"].items():
        if digest((texture_vendor / name).read_bytes()) != expected:
            raise ValueError("Vendored texture encoder source changed: " + name)
    target = args.target_dir
    if not args.skip_compile:
        subprocess.run(
            [
                str(TOOLCHAIN / "cargo"),
                "build",
                "--locked",
                "--manifest-path",
                str(PACKAGE / "builder/Cargo.toml"),
                "--lib",
                "--target",
                "wasm32-unknown-unknown",
                "--release",
                "--target-dir",
                str(target),
            ],
            env=env,
            check=True,
        )
    OUT.mkdir(parents=True, exist_ok=True)
    assets = {}

    def add(name, data, mime):
        if isinstance(data, Path):
            data = data.read_bytes()
        sha = digest(data)
        path = OUT / sha
        if not path.exists():
            path.write_bytes(data)
        item = {
            "sha256": sha,
            "size": len(data),
            "mime": mime,
            "url": f"/api/v1/package-runtime/assets/{sha}",
        }
        precompress(path, item)
        assets[name] = item
        return item

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        subprocess.run(
            [
                str(WASM_BINDGEN),
                str(target / "wasm32-unknown-unknown/release/ts2_package_builder.wasm"),
                "--target",
                "web",
                "--out-dir",
                str(td),
                "--out-name",
                "engine",
            ],
            check=True,
        )
        add("wasm", td / "engine_bg.wasm", "application/wasm")
        add("glue", td / "engine.js", "text/javascript")
    static_root = args.source_static_root or ROOT / "dist/static"
    if not args.source_static_root:
        subprocess.run(
            ["npm", "run", "build", "--", "--out", str(static_root)], cwd=ROOT, check=True
        )
    add(
        "worker",
        (args.source_static_root or ROOT / "dist/static") / "package-runtime/worker.mjs",
        "text/javascript",
    )
    add("archive", static_root / "vendor/package-archive.js", "text/javascript")
    local_model = json.loads((ROOT / "tools/local-upscale/model.json").read_text())
    local_models = json.loads((ROOT / "tools/local-upscale/models.json").read_text())
    for profile in local_models:
        model_path = ROOT / profile["path"]
        if not model_path.exists():
            model_path = Path(os.environ.get("PROJECT_FIXTURE_ROOT", ROOT)) / profile["path"]
        if not model_path.exists():
            raise ValueError(
                "Missing local model " + profile["id"] + ". Follow tools/local-upscale/README.md."
            )
        if (
            model_path.stat().st_size != profile["size"]
            or digest(model_path.read_bytes()) != profile["sha256"]
        ):
            raise ValueError("Local model failed integrity verification: " + profile["id"])
        add(profile["asset"], model_path, "application/octet-stream")
    add("local-upscale-worker", static_root / "local-upscale/worker.mjs", "text/javascript")
    for key, name, mime in [
        ("runtime", "ort.wasm.min.mjs", "text/javascript"),
        ("webgpu-runtime", "ort.webgpu.min.mjs", "text/javascript"),
        ("webgpu-glue", "ort-wasm-simd-threaded.jsep.mjs", "text/javascript"),
        ("webgpu-wasm", "ort-wasm-simd-threaded.jsep.wasm", "application/wasm"),
        ("glue", "ort-wasm-simd-threaded.mjs", "text/javascript"),
        ("wasm", "ort-wasm-simd-threaded.wasm", "application/wasm"),
    ]:
        add("local-upscale-" + key, static_root / "vendor/local-upscale" / name, mime)
    add("object-optimizer", static_root / "vendor/object-optimizer.js", "text/javascript")
    add(
        "object-optimizer-licenses",
        static_root / "vendor/OBJECT-OPTIMIZER-LICENSES.txt",
        "text/plain",
    )
    from package_creation.hair.colors import (
        curves,
        public_palette,
        FAMILIES,
        BASES,
        TEMPLATE,
        TEMPLATE_SHA256,
    )
    from package_creation.hair.colors import ARCHIVE_SHA256

    installed = json.loads((FIXTURES / "hair/assets/manifest.json").read_text())
    assert installed["curve_archive_sha256"] == ARCHIVE_SHA256
    archive = Path.home() / "Downloads/IaKoa-Pooklet Curves.7z"
    if archive.exists():
        assert digest(archive.read_bytes()) == ARCHIVE_SHA256
    palette = {
        "palette": public_palette(),
        "mappings": curves(),
        "families": FAMILIES,
        "bases": BASES,
    }
    palette_bytes = json.dumps(palette, separators=(",", ":")).encode()
    add("palette", palette_bytes, "application/json")
    add("game-meshes", FIXTURES / "hair/assets/game-meshes.json", "application/json")
    conversion = json.loads((FIXTURES / "conversion/assets/manifest.json").read_text())
    for body, profile in conversion["profiles"].items():
        data = (FIXTURES / f"conversion/assets/{body}.zip").read_bytes()
        assert digest(data) == profile["sha256"]
        add(profile["asset"], data, "application/zip")
    template_manifest = json.loads((FIXTURES / "templates/manifest.json").read_text())
    for key, name in [
        ("tattoo-overlay", "Template_MultiOverlay.package"),
        ("tattoo-controller", "Template_OverlayBox.package"),
        ("tattoo-face", "NoFaceOverlay.package"),
    ]:
        path = FIXTURES / "templates" / name
        assert digest(path.read_bytes()) == template_manifest["files"][name]
        add(key, path, "application/octet-stream")
    preview_manifest = json.loads((FIXTURES / "service/preview_assets/manifest.json").read_text())
    for body, info in preview_manifest["bodies"].items():
        path = FIXTURES / f"service/preview_assets/{body}.glb"
        assert digest(path.read_bytes()) == info["glb_sha256"]
        add(f"preview-body:{body}", path, "model/gltf-binary")
    items = []
    for path in sorted((FIXTURES / "hair/assets/standard").glob("*/item.json")):
        item = json.loads(path.read_text())
        item = {k: v for k, v in item.items() if not k.startswith("example_")}
        package = path.parent / "template.package"
        assert digest(package.read_bytes()) == item["template_sha256"]
        item.update(asset="hair:" + item["id"], input_base="Volatile")
        add(item["asset"], package, "application/octet-stream")
        items.append(item)
    assert digest(TEMPLATE.read_bytes()) == TEMPLATE_SHA256
    item = {
        "id": "pooklet-mg-swirl",
        "kind": "standard",
        "label": "Swirl (Mansion & Garden)",
        "game_content": "Mansion & Garden",
        "gender": "Female",
        "ages": ["Young Adult", "Adult", "Elder"],
        "meshes": [],
        "requirements": "Requires Mansion & Garden or The Sims 2 Legacy Collection. Uses the built-in female Swirl mesh.",
        "input_base": "Primer",
        "template_credit": "Pooklet Mansion & Garden Swirl recolor template",
        "asset": "hair:pooklet-mg-swirl",
        "template_sha256": TEMPLATE_SHA256,
    }
    add(item["asset"], TEMPLATE, "application/octet-stream")
    items.append(item)
    object_catalog_path = FIXTURES / "objects/assets/catalog.json"
    object_items = []
    if object_catalog_path.exists():
        object_catalog = json.loads(object_catalog_path.read_text())
        add("object-catalog", object_catalog_path, "application/json")
        add("object-game", FIXTURES / "objects/assets/game.json", "application/json")
        for original in object_catalog["items"]:
            package = FIXTURES / "objects/assets" / (original["id"] + ".package")
            assert digest(package.read_bytes()) == original["sha256"]
            item = {
                k: original[k]
                for k in ("id", "label", "kind", "requirements", "placement", "dimensions")
            }
            item["asset"] = "object:" + item["id"]
            add(item["asset"], package, "application/octet-stream")
            object_items.append(item)
    painting_items = []
    painting_root = FIXTURES / "paintings/assets"
    if (painting_root / "catalog.json").is_file():
        painting_catalog = json.loads((painting_root / "catalog.json").read_text())
        add("painting-catalog", painting_root / "catalog.json", "application/json")
        for original in painting_catalog["items"]:
            ident = original["id"]
            data = (painting_root / (ident + ".package")).read_bytes()
            recipe = json.loads((painting_root / original["recipe"]).read_text())
            if (
                digest(data) != original["sha256"]
                or recipe["template_sha256"] != original["sha256"]
            ):
                raise ValueError("Painting template changed: " + ident)
            item = {
                k: original[k]
                for k in [
                    "id",
                    "label",
                    "shape",
                    "aspect",
                    "dimensions",
                    "placement",
                    "price",
                    "requirements",
                    "sha256",
                ]
            }
            item.update(
                format=recipe["format"],
                asset="painting:" + ident,
                recipe_asset="painting-recipe:" + ident,
                thumbnail_asset="painting-thumbnail:" + ident,
            )
            add(item["asset"], data, "application/octet-stream")
            add(item["recipe_asset"], painting_root / original["recipe"], "application/json")
            add(item["thumbnail_asset"], painting_root / original["thumbnail"], "image/png")
            painting_items.append(item)
    object_reference = json.loads((FIXTURES / "objects/assets/object-reference.json").read_text())
    add("object-reference", FIXTURES / "objects/assets/object-reference.json", "application/json")
    for name in ("am", "af", "table"):
        add(
            "object-reference-" + name,
            FIXTURES / ("objects/assets/reference-" + name + ".glb"),
            "model/gltf-binary",
        )
    sim_items = []
    sim_root = FIXTURES / "sims/assets"
    if (sim_root / "references.json").is_file():
        sim_refs = json.loads((sim_root / "references.json").read_text())
        for body, original in sim_refs["items"].items():
            data = (sim_root / original["file"]).read_bytes()
            if digest(data) != original["sha256"]:
                raise ValueError("Sim rig reference changed: " + body)
            add(original["asset"], data, "application/octet-stream")
            sim_items.append(
                {
                    "id": body,
                    "label": "Adult Male" if body == "am" else "Adult Female",
                    "asset": original["asset"],
                    "sha256": original["sha256"],
                }
            )
    sim_test = (sim_root / "am-everyday-test.package").read_bytes()
    if digest(sim_test) != "c55eea764ee0de814781ccd956969c3a70b34ae81e9c574a4a1658f35fbdae1e":
        raise ValueError("Experimental AM Everyday scaffold changed")
    add("sim-everyday-test", sim_test, "application/octet-stream")
    subprocess.run(
        [
            str(TOOLCHAIN / "cargo"),
            "build",
            "--locked",
            "--release",
            "--example",
            "contracts",
            "--manifest-path",
            str(PACKAGE / "builder/Cargo.toml"),
            "--target-dir",
            str(target),
        ],
        env={**env, "RUSTFLAGS": BUILD_ENV.get("RUSTFLAGS", "")},
        check=True,
    )
    engine_metadata = json.loads(
        subprocess.check_output(
            [str(target / "release/examples/contracts"), "capabilities"], env=env
        )
    )
    manifest = {
        "schema_version": 1,
        "capabilities": engine_metadata["capabilities"],
        "package_compression": engine_metadata["package_compression"],
        "texture_encoders": engine_metadata["texture_encoders"],
        "protocol_version": 1,
        "assets": assets,
        "local_upscale": {
            **local_model,
            "models": local_models,
            "default_model": "compact",
            "execution": "browser-wasm",
            "token_required": False,
            "backends": ["wasm", "webgpu"],
            "processing_timeout_ms": None,
        },
        "hair": {"palette": palette["palette"], "bases": BASES, "items": items},
        "conversion": {
            "version": 1,
            "execution": "browser-wasm",
            "profiles": conversion["profiles"],
            "input_format": "RGBA PNG",
            "operations": [
                "conversion_validate",
                "conversion_begin",
                "conversion_step",
                "conversion_finish",
            ],
        },
        "objects": {
            "items": object_items,
            "sizing_version": 2,
            "fit_to_template": 1,
            "fixed_fitted_height": 1,
            "reference": object_reference,
            "creation": "browser-wasm",
            "workflows": ["model"],
            "model_formats": ["glb", "gltf-zip"],
            "optimization": {
                "worker_asset": "object-optimizer",
                "version": 1,
                "source_triangles": 1000000,
                "source_vertices": 2000000,
                "source_groups": 4096,
                "default_target_triangles": 20000,
            },
        },
        "paintings": {
            "version": 1,
            "creation": "browser-wasm",
            "items": painting_items,
            "operations": [
                "painting_inspect_image",
                "painting_open",
                "painting_compose",
                "painting_prepare",
                "painting_build",
                "painting_validate",
            ],
        },
        "sims": {
            "version": 1,
            "guided_version": 2,
            "creation": "browser-wasm",
            "experimental": True,
            "downloads_enabled": False,
            "gates": {"single_package": "pending", "persistent_head": "pending"},
            "items": sim_items,
            "everyday_test": {
                "version": 1,
                "bodies": ["am"],
                "asset": "sim-everyday-test",
                "head": "stock",
                "gameplay": "not_tested",
            },
            "operations": [
                "sim_align",
                "sim_landmarks",
                "sim_guided_fit",
                "sim_inspect_model",
                "sim_reference",
                "sim_fit",
                "sim_preview",
                "sim_prepare",
                "sim_build",
                "sim_validate",
                "sim_experimental_build",
            ],
        },
        "limits": engine_metadata["limits"],
    }
    if args.source:
        metadata = json.loads(
            subprocess.check_output(
                [
                    str(TOOLCHAIN / "cargo"),
                    "metadata",
                    "--locked",
                    "--manifest-path",
                    str(PACKAGE / "builder/Cargo.toml"),
                    "--format-version",
                    "1",
                ],
                env=env,
            )
        )
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "corresponding-source.tar.gz"
            with deterministic_tar(
                source, int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
            ) as archive:
                for rel in [
                    "__init__.py",
                    "rust-toolchain.toml",
                    "hair/colors.py",
                    "hair/custom_colors.py",
                    "builder/src",
                    "builder/build.rs",
                    "builder/tests",
                    "builder/TEXTURE_ENCODING.md",
                    "builder/vendor",
                    "service/static/texture-compression.js",
                    "builder/examples",
                    "sims/README.md",
                    "builder/Cargo.toml",
                    "builder/Cargo.lock",
                    "service/static/package-runtime",
                    "service/static/app-wasm.js",
                    "service/static/lazy-bootstrap.js",
                    "service/static/conversion-wasm.js",
                    "service/static/hair-wasm.js",
                    "service/static/object-wasm.js",
                    "service/static/object-preview.mjs",
                    "service/static/painting-wasm.js",
                    "service/static/painting-preview.mjs",
                    "service/static/sim-wasm.js",
                    "service/static/sim-preview.mjs",
                    "service/static/scale-scene.mjs",
                    "service/static/hair-curves.js",
                    "service/static/index-wasm.html",
                    "service/static/style.css",
                    "service/PACKAGE_RUNTIME.md",
                ]:
                    source_path = (
                        args.source_static_root / rel.removeprefix("service/static/")
                        if args.source_static_root and rel.startswith("service/static/")
                        else PACKAGE / rel
                    )
                    archive.add(source_path, arcname="package_creation/" + rel)
                for name in [
                    "package.json",
                    "package-lock.json",
                    "web",
                    "tools/archive.py",
                    "tools/project.py",
                    "tools/static_release.py",
                    "tools/static_browser_check.py",
                    "tools/browser_verification.py",
                    "tools/local_upscale_verification.py",
                    "tools/browser_scenarios.py",
                    "tools/body-preview/package.json",
                    "tools/body-preview/build.mjs",
                    "tools/local-upscale",
                    "docs",
                    "README.md",
                    "tests/fixtures",
                    "pyproject.toml",
                    "uv.lock",
                    "eslint.config.mjs",
                    ".node-version",
                    ".prettierignore",
                ]:
                    archive.add(
                        ROOT / name,
                        arcname=name,
                        filter=lambda i: None
                        if "/node_modules" in i.name or "/__pycache__" in i.name
                        else i,
                    )
                archive.add(Path(__file__), arcname="scripts/build_package_runtime.py")
                archive.add(
                    ROOT / "scripts/install_texture_toolchain.py",
                    arcname="scripts/install_texture_toolchain.py",
                )
                archive.add(
                    ROOT / "scripts/extract_object_templates.py",
                    arcname="scripts/extract_object_templates.py",
                )
                archive.add(
                    ROOT / "scripts/build_object_references.mjs",
                    arcname="scripts/build_object_references.mjs",
                )
                archive.add(
                    ROOT / "scripts/install_object_templates.py",
                    arcname="scripts/install_object_templates.py",
                )
                for name in [
                    "extract_painting_templates.py",
                    "install_painting_templates.py",
                    "fetch_sim_fixtures.py",
                    "extract_sim_references.py",
                ]:
                    archive.add(ROOT / "scripts" / name, arcname="scripts/" + name)
                archive.add(
                    PACKAGE / "paintings/README.md", arcname="package_creation/paintings/README.md"
                )
                for name in ["vendor-entry.js", "package.json"]:
                    archive.add(
                        ROOT / "tools/body-preview" / name, arcname="tools/body-preview/" + name
                    )
                for name in [
                    "export_conversion_maps.py",
                    "conversion_margin_recipe.cpp",
                    "conversion_sampling.cpp",
                    "build_conversion_assets.py",
                ]:
                    archive.add(ROOT / "scripts" / name, arcname="scripts/" + name)
                archive.add(PACKAGE / "conversion", arcname="package_creation/conversion")
                for name in [
                    "conversion_reference.py",
                    "conversion_wasm_parity.mjs",
                    "conversion_browser_check.py",
                    "object_browser_check.py",
                ]:
                    archive.add(PACKAGE / "tests" / name, arcname="package_creation/tests/" + name)
                archive.add(PACKAGE / "builder/LICENSE", arcname="LICENSE")
                optimizer = ROOT / "tools/object-optimizer"
                for path in sorted(optimizer.iterdir()):
                    if path.is_file() and path.suffix in {".json", ".mjs", ".md"}:
                        archive.add(path, arcname="tools/object-optimizer/" + path.name)
                for folder in json.loads((static_root / "vendor/vendor-sources.json").read_text()):
                    archive.add(
                        optimizer / folder,
                        arcname=posixpath.normpath("tools/object-optimizer/" + folder),
                    )
                optimizer_source = json.loads((optimizer / "vendor/source.json").read_text())
                source_path = optimizer / "vendor" / optimizer_source["file"]
                assert digest(source_path.read_bytes()) == optimizer_source["sha256"]
                archive.add(optimizer / "vendor", arcname="tools/object-optimizer/vendor")
                import io

                instructions = b"Install WASI SDK 27.0 for C++ texture compilation. scripts/install_texture_toolchain.py installs a checksum-pinned build for supported macOS and Linux hosts. For another host, install the same SDK release and set PACKAGE_WASI_SDK to its directory. Native builds need clang++ (or CXX). The runtime has no WASI imports. Vendored DirectXTex/DirectXMath/SAL provenance and licenses are under package_creation/builder/vendor/directxtex.\nBuild the engine with Rust 1.89 and wasm-bindgen-cli 0.2.127. Run cargo build --locked --manifest-path package_creation/builder/Cargo.toml --lib --release --target wasm32-unknown-unknown with RUSTFLAGS=-C link-arg=--max-memory=1073741824, then wasm-bindgen --target web on the resulting wasm. Sources for every dependency in Cargo.lock are included under dependencies, with Git workspaces under git-workspaces. The published normalized mappings and pinned templates are available separately through the same runtime manifest. Raw GIMP presets are not required to reproduce package processing. See package_creation/service/PACKAGE_RUNTIME.md for asset assembly and serving instructions.\n"
                vendor = Path(temp) / "vendor"
                config = subprocess.check_output(
                    [
                        str(TOOLCHAIN / "cargo"),
                        "vendor",
                        "--locked",
                        "--versioned-dirs",
                        "--manifest-path",
                        str(PACKAGE / "builder/Cargo.toml"),
                        str(vendor),
                    ],
                    env=env,
                    text=True,
                ).replace(str(vendor), "vendor")
                archive.add(vendor, arcname="vendor")
                config_bytes = config.encode()
                config_entry = tarfile.TarInfo(".cargo/config.toml")
                config_entry.size = len(config_bytes)
                archive.addfile(config_entry, io.BytesIO(config_bytes))
                instructions += b"Offline build: from the extracted archive root run cargo build --offline --locked --manifest-path package_creation/builder/Cargo.toml --lib --release --target wasm32-unknown-unknown. The bundled .cargo/config.toml redirects registry and Git dependencies to vendor. Install the matching Rust toolchain and wasm-bindgen CLI first.\n"
                instructions += b"Object optimizer: install Node.js and run npm ci at the source root, then npm run build --workspace tools/object-optimizer. The root workspace lockfile pins npm dependencies. The meshoptimizer C++ source archive, pinned revision and checksum are under tools/object-optimizer/vendor. Its js/Makefile documents regeneration of the embedded simplifier WASM.\n"
                instructions += b"Three.js preview bundle: run npm ci at the source root, then npm run build --workspace tools/body-preview. The root lockfile pins Three.js and esbuild, and the generated bundle retains the MIT license notice.\n"
                entry = tarfile.TarInfo("BUILDING.txt")
                entry.size = len(instructions)
                entry.mtime = 0
                archive.addfile(entry, io.BytesIO(instructions))
                workspaces = set()
                for dep in metadata["packages"]:
                    if dep["source"]:
                        folder = Path(dep["manifest_path"]).parent
                        archive.add(
                            folder,
                            arcname=f"dependencies/{dep['name']}-{dep['version']}",
                            filter=lambda i: (
                                None if "/.git/" in i.name or "/target/" in i.name else i
                            ),
                        )
                        if dep["source"].startswith("git+"):
                            parent = folder
                            while parent.parent != parent and not (parent / ".git").exists():
                                parent = parent.parent
                            if parent.parent != parent and parent not in workspaces:
                                workspaces.add(parent)
                                archive.add(
                                    parent,
                                    arcname=f"git-workspaces/{parent.name}",
                                    filter=lambda i: (
                                        None if "/.git" in i.name or "/target/" in i.name else i
                                    ),
                                )
            add("source", source, "application/gzip")
        notices = "Sims 2 package engine: GPL-3.0-or-later. DBPF library: GPL-3.0-or-later.\nCorresponding source and all dependency license files are in the source archive.\nPooklet and IaKoa: fixed color mappings. Template credits remain in the application and generated instructions.\n\nDependencies from Cargo.lock:\n"
        notices += "\n".join(
            f"{d['name']} {d['version']}: {d.get('license') or 'See bundled source license notices'}"
            for d in metadata["packages"]
        )
        notices += "\n\n" + (static_root / "vendor/OBJECT-OPTIMIZER-LICENSES.txt").read_text()
        notices += "\n\n" + (ROOT / "tools/local-upscale/NOTICES.txt").read_text()
        notices += "\n\n" + (ROOT / "tools/local-upscale/vendor/LICENSE.txt").read_text()
        notices += "\n\n" + (ROOT / "tools/local-upscale/vendor/REALESRGAN-LICENSE").read_text()
        notices += "\n\n" + (static_root / "vendor/local-upscale/LICENSE").read_text()
        notices += "\n\n" + (static_root / "vendor/local-upscale/ThirdPartyNotices.txt").read_text()
        for dependency in ("fast-png", "iobuffer", "pako"):
            notices += "\n\n" + dependency + "\n"
            notices += (ROOT / "node_modules" / dependency / "LICENSE").read_text()
        notices += "\n\n" + (PACKAGE / "conversion/NOTICES.txt").read_text()
        notices += "\n\nDirectXTex and DirectXMath: Microsoft, MIT. SAL: .NET Foundation, MIT.\n"
        for license_file in ["LICENSE", "DirectXMath/LICENSE", "DirectXMath/SAL-LICENSE"]:
            notices += "\n" + (texture_vendor / license_file).read_text()
        add("licenses", notices.encode(), "text/plain")
    else:
        old = OUT / "manifest.json"
        if old.exists():
            for k, v in json.loads(old.read_text())["assets"].items():
                if k in {"source", "licenses"}:
                    assets[k] = v
    # UI is a versioned file graph. Relative imports stay within this release.
    ui_root = OUT / "ui"
    shutil.copytree(static_root, ui_root, dirs_exist_ok=True)
    ui_files = {}
    for path in sorted(ui_root.rglob("*")):
        if not path.is_file() or path.suffix == ".gz":
            continue
        name = path.relative_to(ui_root).as_posix()
        mime = (
            "text/html"
            if path.suffix == ".html"
            else "text/css"
            if path.suffix == ".css"
            else "text/javascript"
            if path.suffix in {".js", ".mjs"}
            else "application/json"
            if path.suffix == ".json"
            else "application/wasm"
            if path.suffix == ".wasm"
            else "text/plain"
        )
        ui_files[name] = file_metadata(path, mime)
    manifest["ui"] = {
        "sha256": digest(json.dumps(ui_files, sort_keys=True, separators=(",", ":")).encode()),
        "files": ui_files,
    }
    page = OUT / "index.html"
    page.write_text(
        (ui_root / "index-wasm.html")
        .read_text()
        .replace("/static/", f"/static/releases/{manifest['ui']['sha256']}/")
    )
    manifest["page"] = file_metadata(page, "text/html")
    manifest["service"] = {
        p.name: {"sha256": digest(p.read_bytes()), "size": p.stat().st_size}
        for p in sorted((PACKAGE / "service").glob("*.py"))
    }
    manifest["build"] = {
        "source_sha256": source_identity(),
        "uv_lock": digest((ROOT / "uv.lock").read_bytes()),
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_date_epoch": int(os.environ.get("SOURCE_DATE_EPOCH", "0")),
        "rust": subprocess.check_output(
            [str(TOOLCHAIN / "rustc"), "--version"], env=env, text=True
        ).strip(),
        "node": subprocess.check_output(["node", "--version"], text=True).strip(),
        "python": subprocess.check_output([sys.executable, "--version"], text=True).strip(),
        "wasm_bindgen": subprocess.check_output(
            [str(WASM_BINDGEN), "--version"], text=True
        ).strip(),
        "clang": subprocess.check_output(
            [str(Path(env["PACKAGE_WASI_SDK"]) / "bin/clang++"), "--version"], text=True
        ).splitlines()[0],
        "cargo_lock": digest((PACKAGE / "builder/Cargo.lock").read_bytes()),
        "npm_lock": digest((ROOT / "package-lock.json").read_bytes()),
    }
    manifest["release"] = digest(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    )
    previous_path = OUT / "manifest.json"
    if previous_path.exists():
        previous = json.loads(previous_path.read_text())
        release = previous["release"]
        assert len(release) == 64 and all(c in "0123456789abcdef" for c in release)
        shutil.copyfile(previous_path, OUT / f"manifest-{release}.json")
    write_delivery(OUT, manifest)
    print(
        json.dumps(
            {
                "release": manifest["release"],
                "wasm_bytes": assets["wasm"]["size"],
                "assets": len(assets),
            }
        )
    )


if __name__ == "__main__":
    main()
