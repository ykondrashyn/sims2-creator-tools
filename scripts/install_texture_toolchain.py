"""Install the checksum-pinned compiler used for browser texture compression."""

from pathlib import Path
import hashlib
import platform
import os
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = "27.0"
PLATFORMS = {
    ("Darwin", "arm64"): (
        "arm64-macos",
        "055c3dc2766772c38e71a05d353e35c322c7b2c6458a36a26a836f9808a550f8",
    ),
    ("Darwin", "x86_64"): (
        "x86_64-macos",
        "163dfd47f989b1a682744c1ae1f0e09a83ff5c4bbac9dcd8546909ab54cda5a1",
    ),
    ("Linux", "x86_64"): (
        "x86_64-linux",
        "b7d4d944c88503e4f21d84af07ac293e3440b1b6210bfd7fe78e0afd92c23bc2",
    ),
    ("Linux", "aarch64"): (
        "arm64-linux",
        "4cf4c553c4640e63e780442146f87d83fdff5737f988c06a6e3b2f0228e37665",
    ),
}
HOST, SHA256 = PLATFORMS[(platform.system(), platform.machine())]
NAME = f"wasi-sdk-{VERSION}-{HOST}"
URL = f"https://github.com/WebAssembly/wasi-sdk/releases/download/wasi-sdk-27/{NAME}.tar.gz"


def main():
    if os.environ.get("PACKAGE_WASI_SDK"):
        print(os.environ["PACKAGE_WASI_SDK"])
        return
    package_root = (
        ROOT / "package_creation" if (ROOT / "package_creation/builder").is_dir() else ROOT
    )
    tools = package_root / ".tools"
    target = tools / NAME
    if (target / "bin/clang++").exists():
        print(target)
        return
    archive = tools / f"{NAME}.tar.gz"
    tools.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        urllib.request.urlretrieve(URL, archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise RuntimeError("Texture compiler archive failed its integrity check")
    with tarfile.open(archive) as source:
        source.extractall(tools, filter="data")
    print(target)


if __name__ == "__main__":
    main()
