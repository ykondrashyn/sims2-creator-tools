"""Generate byte-input oracles from the independently verified DXT family model."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--oracle-root', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
for name, expected in {
    'recovered_rgb.py': 'bf76b10f0fdbdf3149b1110768d64b019b0a758b0bcf7b306e6adbfd11400b25',
    'recovered_dxt.py': '5335c247a31742fc2447ddde5b91e22251f8e0f3943ff7724afc290855467e18',
}.items():
    assert hashlib.sha256((args.oracle_root / name).read_bytes()).hexdigest() == expected
sys.path.insert(0, str(args.oracle_root.resolve()))
from recovered_dxt import encode, INV255
from recovered_rgb import f32

blocks = []
for a in range(256):
    for rgb in [(0, 0, 0), (255, 255, 255), (73, 150, 201)]:
        blocks.append(bytes((*rgb, a)) * 16)
    blocks.append(bytes(v for i in range(16) for v in (17*i, 255-17*i, 64, a)))
for a in (0, 127, 128, 255):
    for count in range(17):
        blocks.append(bytes(v for i in range(16) for v in (17*i, 255-17*i, 64, a if i < count else 255)))
structured = len(blocks)
rng = random.Random(0x44585435)
blocks += [bytes(rng.randrange(256) for _ in range(64)) for _ in range(5000)]
count = len(blocks)
while len(blocks) % 32:
    blocks.append(bytes(64))
width, height = 128, len(blocks) // 32 * 4
image = bytearray(width * height * 4)
expected = {f'DXT{fmt}': bytearray() for fmt in (1, 3, 5)}
for index, block in enumerate(blocks):
    pixels = [[f32(v * INV255) for v in block[i:i+4]] for i in range(0, 64, 4)]
    for fmt in (1, 3, 5):
        expected[f'DXT{fmt}'].extend(encode(pixels, fmt))
    for y in range(4):
        offset = ((index // 32 * 4 + y) * width + index % 32 * 4) * 4
        image[offset:offset+16] = block[y*16:y*16+16]
args.output.mkdir(parents=True, exist_ok=True)
source = args.output / 'bodyshop-family.rgba'
source.write_bytes(image)
paths = {}
for fmt, data in expected.items():
    path = args.output / f'bodyshop-family-{fmt}.bin'
    path.write_bytes(data)
    paths[fmt] = {'path': str(path.resolve()), 'sha256': hashlib.sha256(data).hexdigest()}
corpus_path = args.output / 'corpus.json'
corpus = json.loads(corpus_path.read_text()) if corpus_path.exists() else []
corpus = [c for c in corpus if c['name'] != 'bodyshop-family']
corpus.append(dict(name='bodyshop-family', width=width, height=height, input=str(source.resolve()),
                   expected_by_format=paths, expected_type='synthetic', random_blocks=5000,
                   structured_blocks=structured, padding_blocks=len(blocks)-count))
corpus_path.write_text(json.dumps(corpus, indent=2) + '\n')
print(f'Prepared {count} DXT1/3/5 oracle blocks plus {len(blocks)-count} padding blocks')
