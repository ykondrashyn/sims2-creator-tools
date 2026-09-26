"""Append deterministic blocks from the previously verified Body Shop oracle.

The oracle is development evidence, never shipped as a browser asset. It was
checked against the original instructions before this Rust implementation.
"""
import argparse
import hashlib
import importlib.util
import json
import random
from pathlib import Path

ORACLE_SHA = "eb734f9b9cb8f787932412c749aebde71243dec021d9c65762d37c797ac05020"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert hashlib.sha256(args.oracle.read_bytes()).hexdigest() == ORACLE_SHA
    spec = importlib.util.spec_from_file_location("bodyshop_oracle", args.oracle)
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    blocks = []
    for c in range(256):
        for color in [(c,c,c,255),(c,0,0,255),(0,c,0,255),(0,0,c,255)]:
            blocks.append(bytes(color)*16)
    blocks.append(b"".join(bytes((255,0,0,255) if (i+i//4)%2 else (0,255,0,255)) for i in range(16)))
    rng = random.Random(0x8f5e92)
    for i in range(10000):
        block = bytes(rng.randrange(256) for _ in range(64))
        blocks.append(block)
    count = len(blocks)
    # 1025 byte-input structured cases plus the original non-byte float ramp
    # in builder/tests/bodyshop-traces.json cover the 1026 structured fixtures.
    while len(blocks)%32:
        blocks.append(bytes(64))
    width, height = 128, len(blocks)//32*4
    image = bytearray(width*height*4)
    encoded = bytearray()
    for index, block in enumerate(blocks):
        floats = [[oracle.f32(v*oracle.f32(1/255)) for v in block[i:i+4]] for i in range(0,64,4)]
        rgb = oracle.encode_rgb(floats)
        encoded.extend(bytes(((block[i*8+3]+8)//17)|(((block[i*8+7]+8)//17)<<4) for i in range(8)))
        encoded.extend(rgb)
        for y in range(4):
            start = ((index//32*4+y)*width+index%32*4)*4
            image[start:start+16] = block[y*16:y*16+16]
        if index<64:
            assert oracle.encode_rgb([p[:3]+[0.] for p in floats]) == rgb
        if index%2048==0:
            print(f"Verified synthetic block {index}/{count}", flush=True)
    args.output.mkdir(parents=True,exist_ok=True)
    source = args.output/"synthetic.rgba"
    expected = args.output/"synthetic.bc2"
    source.write_bytes(image)
    expected.write_bytes(encoded)
    corpus_path = args.output/"corpus.json"
    corpus = json.loads(corpus_path.read_text())
    corpus = [c for c in corpus if c["name"]!="synthetic"]
    corpus.append(dict(name="synthetic", width=width, height=height, input=str(source.resolve()), expected=str(expected.resolve()), expected_sha256=hashlib.sha256(encoded).hexdigest(), expected_type="synthetic", random_blocks=10000, structured_blocks=1025))
    corpus_path.write_text(json.dumps(corpus,indent=2)+"\n")


if __name__ == "__main__":
    main()
