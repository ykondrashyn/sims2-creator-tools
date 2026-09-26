"""Index installed game mesh references without copying game resources.

Run this stdlib-only file on the machine with the game, then install its JSON
under hair/assets/game-meshes.json on the server. Only verified resource keys
may satisfy a custom recolor's external game mesh references.
"""
from __future__ import annotations

import argparse
import functools
import json
import hashlib
import re
import struct
from pathlib import Path

CATALOG = Path(__file__).with_name("assets") / "game-meshes.json"
MESH_TYPES = {0xe519c933, 0xfc6eb1f7}


def index_game(game_root: Path, output: Path) -> dict:
    game_root = game_root.resolve()
    records = {}
    packages = sorted(p for p in game_root.rglob("*.package")
                      if p.parent.name in {"Sims3D", "3D"} and p.parent.parent.name == "Res"
                      and p.parent.parent.parent.name == "TSData")
    for path in packages:
        size = path.stat().st_size
        with path.open("rb") as source:
            header = source.read(96)
            if len(header) != 96 or header[:4] != b"DBPF":
                raise ValueError(f"Invalid game package: {path.name}")
            major, minor = struct.unpack_from("<II", header, 4)
            count, offset, length = struct.unpack_from("<III", header, 36)
            index_version = struct.unpack_from("<I", header, 60)[0]
            width = 24 if index_version == 2 else 20
            if (major != 1 or minor not in {0, 1, 2} or index_version not in {0, 1, 2}
                    or length != count * width or length > 64 * 1024 * 1024
                    or offset < 96 or offset + length > size):
                raise ValueError(f"Unsupported game package index: {path.name}")
            source.seek(offset)
            index = source.read(length)
            for start in range(0, length, width):
                entry = struct.unpack_from("<" + "I" * (width // 4), index, start)
                if entry[0] not in MESH_TYPES:
                    continue
                position, packed_size = entry[-2:]
                if position < 96 or not 0 < packed_size <= 64 * 1024 * 1024 or position + packed_size > size:
                    raise ValueError(f"Invalid game mesh bounds: {path.name}")
                source.seek(position)
                digest = hashlib.sha256(source.read(packed_size)).hexdigest()
                instance = entry[2] | ((entry[3] << 32) if width == 24 else 0)
                key = f"{entry[0]:08x}-{entry[1]:08x}-{instance:016x}"
                relative = path.relative_to(game_root).as_posix()
                record = {"canonical_key": key, "source": relative, "sha256": digest,
                          "requirement": "Base game" if relative.startswith("Base/") else "Matching installed game content or The Sims 2 Legacy Collection"}
                # Prefer a base-game copy when an expansion also contains it.
                if key not in records or relative.startswith("Base/"):
                    records[key] = record
    if not records:
        raise ValueError("No game CRES or SHPE resources were found under TSData/Res")
    result = {"schema_version": 1, "installation": game_root.name, "resources": records}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    return result


@functools.lru_cache(maxsize=4)
def _catalog(path: Path, stamp: tuple) -> dict:
    data = json.loads(path.read_text())
    if data.get("schema_version") != 1 or not isinstance(data.get("resources"), dict):
        raise ValueError("The installed game mesh catalog is invalid")
    return data["resources"]


def resolve_game_meshes(keys: set[str], path: Path = CATALOG) -> list[dict]:
    if not path.is_file():
        return []
    stat = path.stat()
    resources = _catalog(path, (stat.st_mtime_ns, stat.st_size))
    found = []
    for key in sorted(keys):
        if not re.fullmatch(r"(?:e519c933|fc6eb1f7)-[0-9a-f]{8}-[0-9a-f]{16}", key):
            continue
        candidates = [key] if key in resources else []
        if not candidates and key.rsplit("-", 1)[1].startswith("00000000"):
            # Old 3IDRs carry only the low 32-bit instance. Accept an alias only
            # when exactly one installed game resource has that type/group/ID.
            candidates = [candidate for candidate in resources
                          if candidate[:18] == key[:18] and candidate[-8:] == key[-8:]]
        if len(candidates) == 1:
            found.append({**resources[candidates[0]], "key": key})
    return found


def requirements(records: list[dict]) -> str:
    if not records:
        return ""
    content = ", ".join(sorted({record["requirement"] for record in records}))
    return f" Also uses installed game meshes: {content}. These game resources are not included in the ZIP."


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = index_game(args.game_root, args.output)
    print(f"Indexed {len(result['resources'])} installed game mesh resources")
