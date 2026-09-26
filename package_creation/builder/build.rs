use std::{env, fs, path::PathBuf, process::Command};

fn run(command: &mut Command) {
    let status = command
        .status()
        .expect("Could not launch the texture compiler");
    assert!(status.success(), "Texture compiler failed");
}

fn main() {
    let root = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap());
    let out = PathBuf::from(env::var_os("OUT_DIR").unwrap());
    let vendor = root.join("vendor/directxtex");
    println!("cargo:rerun-if-changed=vendor/directxtex");
    println!("cargo:rerun-if-changed=src/directxtex.cpp");
    println!("cargo:rerun-if-env-changed=PACKAGE_WASI_SDK");
    println!("cargo:rerun-if-env-changed=CXX");
    let wasm = env::var("TARGET").unwrap().starts_with("wasm32");
    let sdk = env::var_os("PACKAGE_WASI_SDK")
        .map(PathBuf::from)
        .unwrap_or_else(|| root.join("../.tools/wasi-sdk-27.0-arm64-macos"));
    let compiler = if wasm {
        sdk.join("bin/clang++")
    } else {
        PathBuf::from(env::var_os("CXX").unwrap_or_else(|| "clang++".into()))
    };
    if wasm {
        assert!(
            fs::read_to_string(sdk.join("VERSION"))
                .unwrap_or_default()
                .starts_with("27.0\n"),
            "Texture compression requires pinned WASI SDK 27.0"
        );
        assert!(compiler.exists(), "Install the pinned texture compiler with scripts/install_texture_toolchain.py, or set PACKAGE_WASI_SDK to WASI SDK 27.0");
    }
    // Upstream algorithm text stays byte-identical. Only its platform PCH is
    // replaced in the generated translation unit with portable headers.
    let source = fs::read_to_string(vendor.join("BC.cpp")).unwrap().replace(
        "#include \"DirectXTexP.h\"",
        "#include <algorithm>\n#include <cassert>\n#include <cfloat>\n#include <cmath>\n#include <cstdint>\n#include <cstring>\n#include <limits>",
    );
    let translation = out.join("directxtex.cpp");
    fs::write(
        &translation,
        source + &fs::read_to_string(root.join("src/directxtex.cpp")).unwrap(),
    )
    .unwrap();
    let object = out.join("directxtex.o");
    let mut c = Command::new(compiler);
    // DirectXMath stores XMFLOAT4 through HDRColorA pointers. Disable type-based
    // alias assumptions consistently, otherwise LLVM's WASM alpha path differs.
    c.args([
        "-std=c++17",
        "-O2",
        "-ffp-contract=off",
        "-fno-fast-math",
        "-fno-strict-aliasing",
        "-fno-exceptions",
        "-fno-rtti",
        "-DNDEBUG",
        "-D_XM_NO_INTRINSICS_",
        "-Wno-unknown-pragmas",
    ])
    .arg(format!(
        "-ffile-prefix-map={}=/source/builder",
        root.display()
    ))
    .arg(format!("-ffile-prefix-map={}=/build", out.display()))
    .arg("-I")
    .arg(&vendor)
    .arg("-I")
    .arg(vendor.join("DirectXMath"));
    if wasm {
        c.args(["--target=wasm32-wasi", "-mno-simd128", "-mno-bulk-memory"]);
        c.arg(format!(
            "--sysroot={}",
            sdk.join("share/wasi-sysroot").display()
        ));
    }
    run(c.arg("-c").arg(&translation).arg("-o").arg(&object));
    let ar = if wasm {
        sdk.join("bin/llvm-ar")
    } else {
        PathBuf::from("ar")
    };
    run(Command::new(ar)
        .arg("crs")
        .arg(out.join("libts2_directxtex.a"))
        .arg(object));
    println!("cargo:rustc-link-search=native={}", out.display());
    println!("cargo:rustc-link-lib=static=ts2_directxtex");
}
