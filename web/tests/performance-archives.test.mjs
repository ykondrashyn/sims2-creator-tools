import test from "node:test";
import assert from "node:assert/strict";
import { randomFillSync } from "node:crypto";
import { unzipSync } from "fflate";
import { source } from "./helpers.mjs";
const { archive } = await source("package-runtime/zip.ts");
async function build(bytes, damage = false) {
  const blobs = new Map();
  const result = await archive(
    { id: "performance" },
    [{ name: "fixture.package", blob: new Blob([bytes]) }],
    "attempt",
    {
      getBlob: async (key) => {
        const blob = blobs.get(key);
        if (damage && key.includes(":deflate:"))
          return new Blob([new Uint8Array(blob.size)]);
        return blob;
      },
      putBlob: async (key, blob) => blobs.set(key, blob),
      deleteBlob: async (key) => blobs.delete(key),
    },
  );
  const data = new Uint8Array(
    await new Blob(result.parts.map((p) => blobs.get(p))).arrayBuffer(),
  );
  return { result, data };
}
test("ZIP preserves compressed and stored members with fewer materializations", async () => {
  const bytes = randomFillSync(new Uint8Array(1024 ** 2));
  const original = Blob.prototype.arrayBuffer;
  let materialized = 0;
  Blob.prototype.arrayBuffer = function () {
    materialized += this.size;
    return original.call(this);
  };
  let built;
  try {
    built = await build(bytes);
  } finally {
    Blob.prototype.arrayBuffer = original;
  }
  assert.deepEqual(unzipSync(built.data)["fixture.package"], bytes);
  // Includes final assembly in this probe, and retained output readback.
  assert.ok(
    materialized < 5 * 1024 ** 2 + 8192,
    `Materialized ${materialized} bytes`,
  );
  assert.equal(built.result.compression.stored_members, 1);
  const repeating = new Uint8Array(1024 ** 2).fill(7);
  const packed = await build(repeating);
  assert.equal(packed.result.compression.compressed_members, 1);
  assert.deepEqual(unzipSync(packed.data)["fixture.package"], repeating);
  assert.deepEqual((await build(repeating)).data, packed.data);
});
test("compressed stored corruption is still rejected", async () => {
  await assert.rejects(build(new Uint8Array(1024 ** 2).fill(7), true));
});
