"""Build and verify the self-contained GitHub Pages edition, without a service."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
from urllib.parse import urlsplit

from tools.project import ROOT, environment, native_build, run, source_identity, check_toolchain

LIMIT_FILE = 100 * 1024**2
LIMIT_SITE = 1_000_000_000
CSP = (
    "default-src 'self' blob: data:; script-src 'self' blob: 'wasm-unsafe-eval'; "
    "worker-src 'self' blob:; style-src 'self'; img-src 'self' blob: data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'none'"
)
EXTENSIONS = {
    "application/wasm": ".wasm",
    "text/javascript": ".mjs",
    "application/javascript": ".mjs",
    "application/json": ".json",
    "application/gzip": ".tar.gz",
    "application/zip": ".zip",
    "model/gltf-binary": ".glb",
    "text/plain": ".txt",
    "image/png": ".png",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def local_path(site, url):
    parts = urlsplit(url)
    path = Path(parts.path)
    if (
        parts.scheme
        or parts.netloc
        or parts.query
        or parts.fragment
        or path.is_absolute()
        or ".." in path.parts
    ):
        raise ValueError(f"Not a static site-relative URL: {url}")
    target = site / path
    if (
        target.is_symlink()
        or not target.is_file()
        or not target.resolve().is_relative_to(site.resolve())
    ):
        raise ValueError(f"Missing or unsafe static file: {url}")
    return target


def file_info(path):
    return {"sha256": sha(path.read_bytes()), "size": path.stat().st_size}


def assemble(runtime, site):
    """Retain published immutable files so saved batches can recover older engines."""
    manifest = json.loads((runtime / "manifest.json").read_text())
    for value in manifest["assets"].values():
        value.pop("encodings", None)
        value["url"] = "assets/" + value["sha256"] + EXTENSIONS.get(value["mime"], ".bin")
        dest = site / value["url"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(runtime / value["sha256"], dest)
    ui = manifest["ui"]
    for name, info in ui["files"].items():
        info.pop("encodings", None)
    ui["sha256"] = sha(canonical(ui["files"]))
    ui["base"] = f"static/releases/{ui['sha256']}/"
    for name in ui["files"]:
        dest = site / ui["base"] / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(runtime / "ui" / name, dest)
    manifest.pop("release", None)
    manifest.pop("service", None)
    manifest.pop("page", None)
    manifest["deployment"] = {
        "kind": "static",
        "base_path": "/sims2-creator-tools/",
        "root_supported": True,
    }
    manifest["release"] = sha(canonical(manifest))
    page = (runtime / "ui/index-wasm.html").read_text().replace("/static/", ui["base"])
    page = page.replace(
        "<head>",
        "<head>\n"
        + f'<meta http-equiv="Content-Security-Policy" content="{CSP}">\n'
        + f'<meta name="runtime-manifest" content="manifests/{manifest["release"]}.json">',
    )
    (site / "index.html").write_text(page)
    (site / ".nojekyll").touch()
    write_json(site / f"manifests/{manifest['release']}.json", manifest)
    write_json(site / "manifest.json", manifest)
    files = {
        str(p.relative_to(site)): file_info(p)
        for p in sorted(site.rglob("*"))
        if p.is_file() and p.name != "site-integrity.json"
    }
    write_json(
        site / "site-integrity.json",
        {
            "schema_version": 1,
            "source_sha256": source_identity(),
            "release": manifest["release"],
            "files": files,
        },
    )
    return verify(site)


def verify(site):
    site = site.resolve()
    index = json.loads((site / "site-integrity.json").read_text())
    if index["source_sha256"] != source_identity():
        raise ValueError(
            "Prepared site does not match checked-out source. Build a new static release."
        )
    actual = {
        str(p.relative_to(site))
        for p in site.rglob("*")
        if p.is_file() and p.name != "site-integrity.json"
    }
    if actual != set(index["files"]):
        raise ValueError("Static file inventory changed or files are missing.")
    size = 0
    for name, expected in index["files"].items():
        path = local_path(site, name)
        if file_info(path) != expected:
            raise ValueError(f"Static file failed integrity verification: {name}")
        size += expected["size"]
        if expected["size"] >= LIMIT_FILE:
            raise ValueError(f"GitHub individual-file budget exceeded: {name}")
    if size >= LIMIT_SITE:
        raise ValueError(
            "Pages site exceeds 1 GB. Historical assets were retained. Publication stopped."
        )
    for path in (site / "manifests").glob("*.json"):
        m = json.loads(path.read_text())
        release = m.pop("release")
        if sha(canonical(m)) != release or path.stem != release:
            raise ValueError("Invalid immutable manifest identity: " + path.name)
        for a in m["assets"].values():
            if file_info(local_path(site, a["url"])) != {"sha256": a["sha256"], "size": a["size"]}:
                raise ValueError("Runtime asset mismatch: " + a["url"])
        for name, a in m["ui"]["files"].items():
            if file_info(local_path(site, m["ui"]["base"] + name)) != {
                "sha256": a["sha256"],
                "size": a["size"],
            }:
                raise ValueError("Frontend asset mismatch: " + name)
    current = json.loads((site / "manifest.json").read_text())
    if current["release"] != index["release"] or current != json.loads(
        (site / f"manifests/{index['release']}.json").read_text()
    ):
        raise ValueError("Current static release and versioned manifest disagree.")
    if current["build"]["source_sha256"] != index["source_sha256"]:
        raise ValueError("Runtime was built from a different source revision.")
    page = (site / "index.html").read_text()
    if f"manifests/{index['release']}.json" not in page or CSP not in page:
        raise ValueError("Entry page is not bound to this release and its CSP.")
    for name in current["ui"]["files"]:
        if name.startswith("vendor/") or not name.endswith((".js", ".mjs", ".html")):
            continue
        text = (site / current["ui"]["base"] / name).read_text()
        if "/api/" in text or "api.replicate.com" in text or "upscale-token" in text:
            raise ValueError("Server API dependency in static frontend: " + name)
    result = {
        "release": index["release"],
        "files": len(actual),
        "bytes": size,
        "source_sha256": index["source_sha256"],
    }
    print(json.dumps(result, indent=2))
    return result


def build(args):
    env = environment()
    check_toolchain(env)
    stage = ROOT / "artifacts/static-pages/build"
    stage.mkdir(parents=True, exist_ok=True)
    run(["npm", "run", "typecheck"], env=env)
    run(["npm", "run", "lint"], env=env)
    run(["npm", "test"], env=env)
    run(["npm", "run", "build", "--", "--out", stage / "frontend"], env=env)
    target = native_build(env, stage)
    run(
        [
            sys.executable,
            "-m",
            "scripts.build_package_runtime",
            "--target-dir",
            target,
            "--source-static-root",
            stage / "frontend",
            "--output",
            stage / "runtime",
        ],
        env=env,
    )
    assemble(stage / "runtime", args.site)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["build", "verify"])
    parser.add_argument("--site", type=Path, default=ROOT / "site")
    args = parser.parse_args()
    if args.operation == "build":
        build(args)
    else:
        verify(args.site)


if __name__ == "__main__":
    main()
