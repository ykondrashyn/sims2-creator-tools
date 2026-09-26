"""Reproducible development, verification and release entrypoint.

Run with the repository virtual environment: python -m tools.project doctor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "package_creation"
PROFILES = ("fast", "reference", "browser", "release")


def run(command, *, env=None, cwd=ROOT, capture=False):
    print("Running: " + " ".join(map(str, command)), flush=True)
    return subprocess.run(
        list(map(str, command)), cwd=cwd, env=env, check=True, text=True, capture_output=capture
    )


def environment(args=None):
    env = os.environ.copy()
    local = PACKAGE / ".tools"
    rust = getattr(args, "rust_bin", None) or env.get("PACKAGE_RUST_BIN")
    if not rust:
        candidates = sorted((local / "rustup/toolchains").glob("1.89.0-*/bin"))
        rust = str(candidates[0]) if candidates else None
    if rust:
        env["PATH"] = str(rust) + os.pathsep + env.get("PATH", "")
        env["PACKAGE_RUST_BIN"] = str(rust)
    if (local / "cargo").exists():
        env.setdefault("CARGO_HOME", str(local / "cargo"))
        env.setdefault("RUSTUP_HOME", str(local / "rustup"))
    env["RUSTUP_TOOLCHAIN"] = "1.89.0"
    sdk = getattr(args, "wasi_sdk", None) or env.get("PACKAGE_WASI_SDK")
    if not sdk:
        installed = sorted(local.glob("wasi-sdk-27.0-*"))
        sdk = next((p for p in installed if p.is_dir()), None)
    if sdk:
        env["PACKAGE_WASI_SDK"] = str(sdk)
    bindgen = env.get("PACKAGE_WASM_BINDGEN") or shutil.which("wasm-bindgen", path=env["PATH"])
    if not bindgen and (local / "wasm/bin/wasm-bindgen").exists():
        bindgen = str(local / "wasm/bin/wasm-bindgen")
    if bindgen:
        env["PACKAGE_WASM_BINDGEN"] = bindgen
    for key, value in [
        ("BROWSER_BINARY", getattr(args, "browser_binary", None)),
        ("PROJECT_FIXTURE_ROOT", getattr(args, "fixture_root", None)),
    ]:
        if value:
            env[key] = str(Path(value).resolve())
    env["RUSTFLAGS"] = f"--remap-path-prefix={ROOT}=/sims2-creator-tools"
    return env


def source_identity():
    """Hash checked-out source, including new files but excluding ignored data."""
    paths = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT
    ).split(b"\0")
    digest = hashlib.sha256()
    for name in sorted(set(paths)):
        if not name or name.startswith(b"site/"):
            continue
        path = ROOT / os.fsdecode(name)
        if path.is_file():
            digest.update(name + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def identity(env):
    versions = {}
    for name, command in {
        "python": [sys.executable, "--version"],
        "node": ["node", "--version"],
        "npm": ["npm", "--version"],
        "rust": ["rustc", "--version"],
        "cargo": ["cargo", "--version"],
        "wasm_bindgen": [env.get("PACKAGE_WASM_BINDGEN", "wasm-bindgen"), "--version"],
        "uv": [sys.executable, "-m", "uv", "--version"],
    }.items():
        try:
            versions[name] = subprocess.check_output(command, env=env, text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            versions[name] = "unavailable"
    return {
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_sha256": source_identity(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
        "versions": versions,
    }


def check_toolchain(env):
    versions = identity(env)["versions"]
    expected = {
        "python": "Python 3.11.14",
        "node": "v22.18.0",
        "npm": "10.9.3",
        "rust": "rustc 1.89.0 ",
        "cargo": "cargo 1.89.0 ",
        "wasm_bindgen": "wasm-bindgen 0.2.127",
        "uv": "uv 0.9.5",
    }
    mismatches = {
        k: {"expected": v, "actual": versions[k]}
        for k, v in expected.items()
        if not versions[k].startswith(v)
    }
    sdk = Path(env.get("PACKAGE_WASI_SDK", ""))
    if not (sdk / "bin/clang++").is_file():
        mismatches["wasi_sdk"] = "Configure the pinned WASI SDK 27.0 compiler."
    if mismatches:
        raise RuntimeError("Toolchain does not match the release pins: " + json.dumps(mismatches))


def doctor(args):
    env = environment(args)
    result = identity(env)
    result["paths"] = {
        k: env.get(k)
        for k in [
            "PACKAGE_RUST_BIN",
            "PACKAGE_WASI_SDK",
            "PACKAGE_WASM_BINDGEN",
            "PROJECT_FIXTURE_ROOT",
            "BROWSER_BINARY",
        ]
    }
    result["fixtures"] = check_fixtures(env, required=False)
    print(json.dumps(result, indent=2))
    check_toolchain(env)


def bootstrap(args):
    env = environment(args)
    run(
        [sys.executable, "-m", "uv", "sync", "--locked", "--extra", "test", "--extra", "offline"],
        env=env,
    )
    run(["npm", "ci"], env=env)
    rustup = shutil.which("rustup", path=env["PATH"]) or str(PACKAGE / ".tools/cargo/bin/rustup")
    run(
        [
            rustup,
            "toolchain",
            "install",
            "1.89.0",
            "--profile",
            "minimal",
            "--component",
            "clippy,rustfmt",
            "--target",
            "wasm32-unknown-unknown",
        ],
        env=env,
    )
    if not env.get("PACKAGE_WASM_BINDGEN"):
        run(["cargo", "install", "wasm-bindgen-cli", "--version", "0.2.127", "--locked"], env=env)
    run([sys.executable, "scripts/install_texture_toolchain.py"], env=env)
    doctor(args)


def check_fixtures(env, *, required=True):
    registry = json.loads((ROOT / "tests/fixtures/registry.json").read_text())
    root = Path(env.get("PROJECT_FIXTURE_ROOT", ROOT))
    result = []
    for item in registry["files"]:
        path = root / item["path"]
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        state = "ok" if actual == item["sha256"] else "missing" if actual is None else "changed"
        result.append({"path": item["path"], "state": state})
    failed = [r for r in result if r["state"] != "ok"]
    if failed and required:
        raise RuntimeError(
            "Required reference fixtures failed verification: "
            + json.dumps(failed)
            + ". See tests/fixtures/README.md."
        )
    return result


def native_build(env, out):
    target = out / "target"
    run(
        [
            "cargo",
            "build",
            "--locked",
            "--release",
            "--bins",
            "--examples",
            "--manifest-path",
            PACKAGE / "builder/Cargo.toml",
            "--target-dir",
            target,
        ],
        env=env,
    )
    native = target / "release"
    env.update(
        PROJECT_NATIVE_ROOT=str(native),
        TS2_HAIR_BUILDER=str(native / "ts2-hair-builder"),
        TS2_PACKAGE_BUILDER=str(native / "ts2-package-builder"),
        SIM_NATIVE_BINARY=str(native / "examples/sim"),
        SIM_GUIDED_BINARY=str(native / "examples/sim"),
        OBJECT_NATIVE_BINARY=str(native / "examples/object"),
        PAINTING_NATIVE_BINARY=str(native / "examples/painting"),
        TEXTURE_NATIVE=str(native / "examples/texture_codec"),
        RUNTIME_NATIVE=str(native / "examples/runtime"),
        NATIVE_RUNTIME=str(native / "examples/runtime"),
        NATIVE_OBJECT=str(native / "examples/object"),
        NATIVE_PAINTING=str(native / "examples/painting"),
        NATIVE_SIM=str(native / "examples/sim"),
    )
    return target


def wasm_build(env, target, out):
    wasm_env = {
        **env,
        "RUSTFLAGS": env.get("RUSTFLAGS", "") + " -C link-arg=--max-memory=1073741824",
    }
    run(
        [
            "cargo",
            "build",
            "--locked",
            "--release",
            "--lib",
            "--target",
            "wasm32-unknown-unknown",
            "--manifest-path",
            PACKAGE / "builder/Cargo.toml",
            "--target-dir",
            target,
        ],
        env=wasm_env,
    )
    glue = out / "engine"
    run(
        [
            env.get("PACKAGE_WASM_BINDGEN", "wasm-bindgen"),
            target / "wasm32-unknown-unknown/release/ts2_package_builder.wasm",
            "--target",
            "web",
            "--out-dir",
            glue,
            "--out-name",
            "engine",
        ],
        env=env,
    )
    env["TEXTURE_ENGINE_ROOT"] = str(glue)
    return glue


def main():
    parser = argparse.ArgumentParser(description="Pinned offline development toolchains")
    parser.add_argument("--rust-bin", type=Path)
    parser.add_argument("--wasi-sdk", type=Path)
    parser.add_argument("--fixture-root", type=Path)
    parser.add_argument("--browser-binary", type=Path)
    parser.add_argument("command", choices=["doctor", "bootstrap"])
    args = parser.parse_args()
    globals()[args.command](args)


if __name__ == "__main__":
    main()
