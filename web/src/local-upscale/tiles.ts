/** Match RealESRGANer RGB normalization, reflected pre-pad and overlap cropping. */
export const SCALE = 4;
export const TILE = 128;
export const OVERLAP = 64;
export const PREPAD = 10;
export const MAX_INPUT_PIXELS = 4_000_000;

export function checkDimensions(width: number, height: number) {
  if (
    !Number.isInteger(width) ||
    !Number.isInteger(height) ||
    width < 1 ||
    height < 1 ||
    width > 2048 ||
    height > 2048 ||
    width * height > MAX_INPUT_PIXELS
  )
    throw new Error(
      "Local upscaling accepts up to 4 megapixels and 2048 pixels per side.",
    );
}

export function reflect(position: number, length: number): number {
  if (length === 1) return 0;
  const period = (length - 1) * 2;
  const wrapped = ((position % period) + period) % period;
  return wrapped < length ? wrapped : period - wrapped;
}

export function* tiles(
  width: number,
  height: number,
  size = TILE,
  overlap = OVERLAP,
  prepad = PREPAD,
) {
  checkDimensions(width, height);
  if (!Number.isInteger(size) || size < 1 || size > TILE)
    throw new Error("Invalid tile size.");
  for (let y = 0; y < height; y += size) {
    for (let x = 0; x < width; x += size) {
      yield {
        x,
        y,
        width: Math.min(size, width - x),
        height: Math.min(size, height - y),
        left: Math.max(0, x - overlap),
        top: Math.max(0, y - overlap),
        right: Math.min(width + prepad, x + size + overlap),
        bottom: Math.min(height + prepad, y + size + overlap),
      };
    }
  }
}
export type Tile =
  ReturnType<typeof tiles> extends Generator<infer T> ? T : never;

export function tileInput(
  rgb: Uint8Array,
  width: number,
  height: number,
  tile: Tile,
) {
  const w = tile.right - tile.left,
    h = tile.bottom - tile.top;
  const pixels = w * h;
  const input = new Float32Array(pixels * 3);
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      const source =
        (reflect(y + tile.top, height) * width +
          reflect(x + tile.left, width)) *
        3;
      const index = y * w + x;
      for (let c = 0; c < 3; c++)
        input[c * pixels + index] = rgb[source + c] / 255;
    }
  return input;
}

export function toByte(value: number) {
  if (!Number.isFinite(value))
    throw new Error("The local model produced invalid pixels.");
  const scaled = Math.fround(Math.max(0, Math.min(1, value)) * 255);
  const lower = Math.floor(scaled),
    fraction = scaled - lower;
  return fraction === 0.5 ? lower + (lower % 2) : Math.round(scaled);
}

export function writeTile(
  target: Uint8Array,
  width: number,
  tile: Tile,
  data: Float32Array,
  scale = SCALE,
) {
  const outWidth = (tile.right - tile.left) * scale;
  const plane = outWidth * (tile.bottom - tile.top) * scale;
  if (data.length !== plane * 3)
    throw new Error("The local model returned an unexpected image size.");
  for (let y = 0; y < tile.height * scale; y++)
    for (let x = 0; x < tile.width * scale; x++) {
      const from =
        (y + (tile.y - tile.top) * scale) * outWidth +
        x +
        (tile.x - tile.left) * scale;
      const to =
        ((tile.y * scale + y) * width * scale + tile.x * scale + x) * 3;
      for (let c = 0; c < 3; c++)
        target[to + c] = toByte(data[c * plane + from]);
    }
}
