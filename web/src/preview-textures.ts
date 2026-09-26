// Shared texture preparation for both viewers. Package inputs remain untouched.
export function visibleLayers(
  entries: any[],
  gender: string,
  hiddenIds: Set<unknown>,
) {
  return entries
    .filter((entry) => entry[gender] && !hiddenIds.has(entry.id))
    .sort((a: { layer: number }, b: { layer: number }) => a.layer - b.layer);
}

export async function decodeTexture(file: Blob) {
  if (file.size > 8 * 1024 * 1024)
    throw new Error("PNG must be 8 MiB or smaller.");
  const header = new Uint8Array(await file.slice(0, 33).arrayBuffer());
  const signature = [137, 80, 78, 71, 13, 10, 26, 10];
  if (
    header.length < 33 ||
    signature.some((byte, index) => header[index] !== byte) ||
    String.fromCharCode(...header.slice(12, 16)) !== "IHDR"
  ) {
    throw new Error("Choose a valid TS2 texture PNG.");
  }
  const view = new DataView(header.buffer);
  if (view.getUint32(16) !== 1024 || view.getUint32(20) !== 1024) {
    throw new Error(
      "Use a 1024 × 1024 TS2 texture. Convert TS4 textures in the texture tab first.",
    );
  }
  if (header[24] !== 8 || header[25] !== 6)
    throw new Error("Use an 8-bit RGBA PNG with an alpha channel.");
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    return image;
  } catch {
    throw new Error("This PNG could not be decoded. Export the texture again.");
  } finally {
    URL.revokeObjectURL(url);
  }
}

export function compositeTextures(
  canvas: HTMLCanvasElement,
  images: any[],
  skinColor: any,
) {
  const context = canvas.getContext("2d", { colorSpace: "srgb" });
  if (!context)
    throw new Error("The browser could not create a texture preview canvas.");
  context.globalAlpha = 1;
  context.globalCompositeOperation = "source-over";
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = skinColor;
  context.fillRect(0, 0, canvas.width, canvas.height);
  for (const image of images)
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
}
