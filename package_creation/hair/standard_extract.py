"""Extract a pinned standard catalog from a game installation, using stdlib only.

This tool runs on the machine holding the game. It reads game files without
modifying them. Only server-side templates are created by the later installer.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path
def unpack_refpack(data):
    """Decode the resource bytes for a hash check during offline extraction."""
    if data[4:6] != b"\x10\xfb":
        return data
    output, pos = bytearray(), 9
    expected = int.from_bytes(data[6:9], "big")
    if expected > 64 * 1024 * 1024:
        raise ValueError("Game resource exceeds 64 MiB")
    while pos < len(data):
        command = data[pos]
        pos += 1
        if command >= 0xfc:
            output.extend(data[pos:pos + (command & 3)])
            break
        if command >= 0xe0:
            length = ((command & 31) << 2) + 4
            output.extend(data[pos:pos + length])
            pos += length
            continue
        if command >= 0xc0:
            a, b, c = data[pos:pos + 3]
            pos += 3
            literal, offset, length = command & 3, ((command & 16) << 12) + (a << 8) + b + 1, ((command & 12) << 6) + c + 5
        elif command >= 0x80:
            a, b = data[pos:pos + 2]
            pos += 2
            literal, offset, length = a >> 6, ((a & 63) << 8) + b + 1, (command & 63) + 4
        else:
            a = data[pos]
            pos += 1
            literal, offset, length = command & 3, ((command & 96) << 3) + a + 1, ((command & 28) >> 2) + 3
        output.extend(data[pos:pos + literal])
        pos += literal
        for _ in range(length):
            output.append(output[-offset])
    if len(output) != expected:
        raise ValueError("Truncated game resource")
    return bytes(output)


def extract(game_root: Path, recipes: Path, output: Path) -> None:
    catalog = json.loads(recipes.read_text())
    indexes = {}
    output.mkdir(parents=True, exist_ok=True)
    for item in catalog["items"]:
        resources = []
        for record in item["resources"]:
            path = (game_root / record["source"]).resolve()
            if not path.is_relative_to(game_root.resolve()):
                raise ValueError("Game source path escapes the installation")
            if path not in indexes:
                with path.open("rb") as source:
                    header = source.read(96)
                    if header[:4] != b"DBPF":
                        raise ValueError(f"Not a DBPF game file: {path.name}")
                    count, offset, length = struct.unpack_from("<III", header, 36)
                    if not count or length // count not in (20, 24) or length > 64 * 1024 * 1024:
                        raise ValueError("Unsupported game package index")
                    width = length // count
                    source.seek(offset)
                    index = source.read(length)
                entries = {}
                for start in range(0, length, width):
                    v = struct.unpack_from("<" + "I" * (width // 4), index, start)
                    key = f"{v[0]:08x}-{v[1]:08x}-{v[2] + ((v[3] << 32) if width == 24 else 0):016x}"
                    entries[key] = v[-2:]
                indexes[path] = entries
            offset, length = indexes[path][record["key"]]
            if length > 64 * 1024 * 1024:
                raise ValueError("Game resource exceeds 64 MiB")
            with path.open("rb") as source:
                source.seek(offset)
                data = unpack_refpack(source.read(length))
            if hashlib.sha256(data).hexdigest() != record["sha256"]:
                raise ValueError(f"Game resource differs from the verified catalog: {record['key']}")
            resources.append((record["key"], data))
        result = bytearray(96)
        result[:4] = b"DBPF"
        for offset, value in [(4, 1), (8, 1), (32, 7), (60, 2)]:
            struct.pack_into("<I", result, offset, value)
        index = bytearray()
        for key, data in resources:
            kind, group, instance = (int(part, 16) for part in key.split("-"))
            index.extend(struct.pack("<6I", kind, group, instance & 0xffffffff, instance >> 32, len(result), len(data)))
            result.extend(data)
        struct.pack_into("<III", result, 36, len(resources), len(result), len(index))
        result.extend(index)
        (output / (item["id"] + ".package")).write_bytes(result)
        print(f"Extracted {item['id']}: {len(resources)} verified resources", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-root", type=Path, required=True)
    parser.add_argument("--recipes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    extract(args.game_root, args.recipes, args.output)
