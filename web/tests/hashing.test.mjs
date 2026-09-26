import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { source } from "./helpers.mjs";
const { Sha256 } = await source("package-runtime/streaming-sha256.ts");
const { sha256 } = await source("package-runtime/sha256.ts");
for (const length of [0, 1, 55, 56, 63, 64, 65, 127, 128, 255, 1048579])
  test(`SHA-256 exact parity, ${length} bytes`, () => {
    const bytes = Uint8Array.from(
      { length },
      (_, i) => (i * 29 + (i >> 6)) & 255,
    );
    const expected = createHash("sha256").update(bytes).digest("hex");
    for (const stride of [1, 57, 64, 65536]) {
      const hash = new Sha256();
      for (let i = 0; i < bytes.length; i += stride)
        hash.update(bytes.subarray(i, i + stride));
      assert.equal(hash.digest(), expected);
      assert.throws(() => hash.digest(), /finalized/);
    }
    assert.equal(sha256(bytes), expected);
  });
