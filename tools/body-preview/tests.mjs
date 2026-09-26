import assert from "node:assert/strict";
import {test} from "node:test";
import {createCanvas} from "@napi-rs/canvas";
import {visibleLayers, compositeTextures, decodeTexture} from "../../package_creation/service/static/preview-textures.mjs";
import {BodyPreview} from "../../package_creation/service/static/body-preview.mjs";
import {RepeatWrapping} from "../../package_creation/service/static/vendor/three-preview.js";
import {RepeatWrapping as expectedRepeatWrapping} from "three";

test("painting UV repeat mode is exported by the shared preview bundle", () => {
  assert.equal(RepeatWrapping, expectedRepeatWrapping);
});

function solid(color) {
  const canvas = createCanvas(2, 2);
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = color;
  ctx.fillRect(0, 0, 2, 2);
  return canvas;
}
const pixel = canvas => [...canvas.getContext("2d").getImageData(0, 0, 1, 1).data];

test("composite respects alpha, skin and layer priority, independent of menu order", () => {
  const red = solid("rgba(255,0,0,0.5)");
  const blue = solid("rgba(0,0,255,0.5)");
  const entries = [{id: "top", layer: 1, am: blue}, {id: "bottom", layer: 0, am: red}];
  const canvas = createCanvas(2, 2);
  compositeTextures(canvas, visibleLayers(entries, "am", new Set()).map(e => e.am), "#000000");
  const [r, g, b, a] = pixel(canvas);
  assert.ok(Math.abs(r - 64) <= 1 && g === 0 && Math.abs(b - 128) <= 1 && a === 255);
  assert.deepEqual(entries.map(e => e.id), ["top", "bottom"]);
  compositeTextures(canvas, visibleLayers(entries, "am", new Set(["top"])).map(e => e.am), "#000000");
  assert.deepEqual(pixel(canvas), [127, 0, 0, 255]);
  compositeTextures(canvas, visibleLayers(entries, "af", new Set()).map(e => e.af), "#c99b7c");
  assert.deepEqual(pixel(canvas), [201, 155, 124, 255]);
});

test("transparent pixels preserve the opaque mannequin and previous results are cleared", () => {
  const canvas = createCanvas(2, 2);
  compositeTextures(canvas, [solid("#0000ff")], "#ffffff");
  compositeTextures(canvas, [solid("rgba(255,0,0,0)")], "#123456");
  assert.deepEqual(pixel(canvas), [18, 52, 86, 255]);
});

test("bad format, oversized dimensions and missing alpha are rejected before decoding", async () => {
  await assert.rejects(decodeTexture(new Blob(["not a PNG"])), /valid TS2/);
  const canvas = createCanvas(1024, 2048);
  await assert.rejects(decodeTexture(new Blob([canvas.toBuffer("image/png")])), /1024 × 1024/);
  const rgba = createCanvas(1024, 1024).toBuffer("image/png");
  const rgb = Buffer.from(rgba);
  rgb[25] = 2;
  await assert.rejects(decodeTexture(new Blob([rgb])), /RGBA/);
  await assert.rejects(decodeTexture(new Blob([new Uint8Array(8 * 1024 * 1024 + 1)])), /8 MiB/);
});

test("latest upload wins when an earlier image finishes decoding later", async () => {
  let finishOld;
  const delayed = new Promise(resolve => { finishOld = resolve; });
  const blue = solid("#0000ff");
  const red = solid("#ff0000");
  const body = {userData: {size: {}}};
  const preview = {
    revision: 0, gender: "am", entries: [{id: "old", layer: 0, am: red}], hiddenIds: new Set(),
    status: {}, root: {dataset: {}}, skin: {value: "#ffffff"}, texture: {},
    canvas: createCanvas(2, 2), scene: {add() {}, remove() {}}, size: {copy() {}},
    model: async () => body, image: file => file === red ? delayed : Promise.resolve(blue), invalidate() {},
  };
  const old = BodyPreview.prototype.refresh.call(preview);
  preview.entries = [{id: "new", layer: 0, am: blue}];
  await BodyPreview.prototype.refresh.call(preview);
  finishOld(red);
  await old;
  assert.deepEqual(pixel(preview.canvas), [0, 0, 255, 255]);
  assert.equal(preview.root.dataset.state, "ready");
  assert.match(preview.status.textContent, /1 visible tattoo/);
});

test("invalid textures are reported and do not leave old tattoo pixels", async () => {
  const canvas = solid("#0000ff");
  const preview = {
    revision: 0, gender: "af", entries: [{id: "bad", label: "Broken", layer: 0, af: {}}], hiddenIds: new Set(),
    status: {}, root: {dataset: {}}, skin: {value: "#123456"}, texture: {}, canvas,
    scene: {add() {}, remove() {}}, size: {copy() {}}, model: async () => ({userData: {size: {}}}),
    image: async () => {throw new Error("Invalid PNG");}, invalidate() {},
  };
  await BodyPreview.prototype.refresh.call(preview);
  assert.deepEqual(pixel(canvas), [18, 52, 86, 255]);
  assert.equal(preview.root.dataset.state, "error");
  assert.match(preview.status.textContent, /Broken: Invalid PNG/);
});

test("missing WebGL is contained in the viewer with an actionable fallback", () => {
  const oldDocument = globalThis.document;
  const oldError = console.error;
  const requestedContexts = [];
  const root = {
    querySelector() { return {value: "#c99b7c"}; },
    replaceChildren() { this.cleared = true; },
  };
  try {
    globalThis.document = {
      createElement() { return createCanvas(1, 1); },
      createElementNS() {
        return {width: 1, height: 1, style: {}, setAttribute() {}, addEventListener() {},
          getContext(kind) { requestedContexts.push(kind); return null; }};
      },
    };
    console.error = () => {};
    assert.throws(() => new BodyPreview(root), /WebGL 2.*package creation are still available/);
    assert.ok(requestedContexts.includes("webgl2"));
    assert.equal(root.cleared, true);
  } finally {
    globalThis.document = oldDocument;
    console.error = oldError;
  }
});
