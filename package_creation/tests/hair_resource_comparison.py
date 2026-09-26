"""Compare the supplied Rose archive's used DXT mip payloads with fresh outputs."""
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import zipfile
from package_creation.hair.standard_extract import unpack_refpack


def resources(data):
    count, offset, length = struct.unpack_from("<III", data, 36)
    width = length // count
    for start in range(offset, offset + length, width):
        row = struct.unpack_from("<" + "I" * (width // 4), data, start)
        yield row[0], unpack_refpack(data[row[-2]:row[-2] + row[-1]])


class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, n):
        value = self.data[self.pos:self.pos + n]
        assert len(value) == n
        self.pos += n
        return value

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def string(self):
        value = shift = 0
        while True:
            byte = self.take(1)[0]
            value |= (byte & 127) << shift
            shift += 7
            if byte < 128:
                return self.take(value).decode()


def texture(data):
    r = Reader(data)
    assert r.u32() == 0xffff0001
    r.take(r.u32() * 16)
    assert r.u32() == 1
    assert r.u32() == 0x1c4a276c
    r.string()
    assert r.u32() == 0x1c4a276c
    version = r.u32()
    assert r.string() == "cSGResource"
    assert (r.u32(), r.u32()) == (0, 2)
    name = r.string()
    dimensions = (r.u32(), r.u32())
    fmt, mips = r.u32(), r.u32()
    r.take(4)
    assert r.u32() == 1
    r.u32()
    if version == 9:
        r.string()
        assert r.u32() == mips
    payloads = []
    for _ in range(mips):
        assert r.take(1) == b"\0"
        payloads.append(hashlib.sha256(r.take(r.u32())).hexdigest())
    # The source-instance suffix identifies the atlas without relying on new
    # batch groups. Keep younger and Elder variants distinct in the comparison.
    role = re.search(r"hair-[0-9a-f]{8}-([0-9a-f]{16}(?:-elder)?)_txtr$", name).group(1)
    return role, dict(dimensions=dimensions, format=fmt, mips=mips, payloads=payloads)


def compare(archive, generated, output):
    rows = []
    with zipfile.ZipFile(archive) as old:
        for member in old.namelist():
            if not member.startswith("loste_rose72_") or not member.endswith(".package"):
                continue
            color = member.removeprefix("loste_rose72_")
            current = (generated / ("Rose_" + color)).read_bytes()
            previous = old.read(member)
            current_resources = list(resources(current))
            old_textures = dict(texture(data) for kind, data in resources(previous) if kind == 0x1c4a276c)
            new_textures = dict(texture(data) for kind, data in current_resources if kind == 0x1c4a276c)
            assert len(new_textures) == 5
            assert sum(kind == 0x49596978 for kind, _ in current_resources) == 18
            for role, pixels in new_textures.items():
                assert pixels == old_textures[role], (color, role)
            rows.append(dict(color=color.removesuffix(".package"), before=len(previous), after=len(current),
                             saving_percent=100 * (1 - len(current) / len(previous)),
                             encoded_mips_byte_identical=True, textures=5, materials=18))
    output.write_text(json.dumps(rows, indent=2))
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    compare(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
