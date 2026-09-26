import test from "node:test";
import assert from "node:assert/strict";
import { source } from "./helpers.mjs";
const core = await source("local-upscale/tiles.ts");

test("Local model dimensions reject oversized outputs before allocation", () => {
  for (const pair of [
    [0, 5],
    [2049, 1],
    [2001, 2000],
    [NaN, 5],
    [1.5, 2],
  ])
    assert.throws(() => core.checkDimensions(...pair));
  core.checkDimensions(2000, 2000);
  core.checkDimensions(1, 1);
});

test("Reflection handles edges and tiny images without flattening RGB", () => {
  assert.deepEqual(
    Array.from({ length: 9 }, (_, x) => core.reflect(x, 3)),
    [0, 1, 2, 1, 0, 1, 2, 1, 0],
  );
  assert.equal(core.reflect(99, 1), 0);
  const tile = [...core.tiles(1, 1)][0];
  const data = core.tileInput(new Uint8Array([77, 99, 121]), 1, 1, tile);
  const n = data.length / 3;
  for (let c = 0; c < 3; c++)
    assert.ok(
      data
        .subarray(c * n, (c + 1) * n)
        .every((value) => value === Math.fround([77, 99, 121][c] / 255)),
    );
});

test("Overlapping tiles write every destination exactly once in RGB order", () => {
  const width = 131,
    height = 129;
  const target = new Uint8Array(width * height * 16 * 3);
  const touched = new Uint8Array(width * height * 16);
  for (const tile of core.tiles(width, height)) {
    const w = (tile.right - tile.left) * 4,
      h = (tile.bottom - tile.top) * 4;
    const data = new Float32Array(w * h * 3);
    for (let c = 0; c < 3; c++)
      data.fill((c + 1) / 4, c * w * h, (c + 1) * w * h);
    core.writeTile(target, width, tile, data);
    for (let y = 0; y < tile.height * 4; y++)
      for (let x = 0; x < tile.width * 4; x++)
        touched[(tile.y * 4 + y) * width * 4 + tile.x * 4 + x]++;
  }
  assert.ok(touched.every((value) => value === 1));
  for (let i = 0; i < target.length; i += 3)
    assert.deepEqual([...target.subarray(i, i + 3)], [64, 128, 191]);
  assert.throws(() =>
    core.writeTile(
      target,
      width,
      [...core.tiles(width, height)][0],
      new Float32Array(1),
    ),
  );
});

test("Pixel packing uses FP32 multiplication, nearest-even rounding and clamping", () => {
  assert.equal(core.toByte(-10), 0);
  assert.equal(core.toByte(10), 255);
  assert.equal(core.toByte(0.5), 128);
  assert.equal(core.toByte(2.5 / 255), 2);
  assert.equal(core.toByte(3.5 / 255), 4);
  assert.throws(() => core.toByte(Infinity));
  assert.throws(() => core.toByte(NaN));
});
