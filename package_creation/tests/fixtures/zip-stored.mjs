// Streaming ZIP with stored members. DBPF textures are already compressed.
const table = Uint32Array.from({ length: 256 }, (_, n) => {
  for (let i = 0; i < 8; i++) n = n & 1 ? 0xedb88320 ^ (n >>> 1) : n >>> 1;
  return n >>> 0;
});
export function crc32(bytes, crc = 0) {
  let c = crc ^ 0xffffffff;
  for (const b of bytes) c = table[(c ^ b) & 255] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function header(size) {
  const b = new Uint8Array(size);
  return [b, new DataView(b.buffer)];
}
async function checksum(blob) {
  let crc = 0;
  for (let n = 0; n < blob.size; n += 4 * 1024 ** 2) {
    crc = crc32(
      new Uint8Array(await blob.slice(n, n + 4 * 1024 ** 2).arrayBuffer()),
      crc,
    );
  }
  return crc;
}
export async function archive(job, entries, token, { getBlob, putBlob }) {
  let offset = 0;
  const parts = [],
    central = [];
  const names = new Set();
  let index = 0;
  async function emit(blob) {
    if (offset + blob.size > 512 * 1024 ** 2)
      throw new Error("Archive exceeds 512 MiB. Reduce the batch size or its textures.");
    const key = `job:${job.id}:zip:${token}:${index++}`;
    // Materialize each bounded chunk before storing it. In Firefox, re-storing
    // a slice of an IndexedDB-backed Blob can retain the wrong backing range.
    const bytes = await blob.arrayBuffer();
    await putBlob(key, new Blob([bytes], {type: blob.type}), token);
    const stored = await getBlob(key);
    if (
      !stored ||
      stored.size !== blob.size ||
      (await checksum(stored)) !== crc32(new Uint8Array(bytes))
    )
      throw new Error(
        "Stored ZIP chunk failed CRC verification. Resume the saved batch.",
      );
    parts.push(key);
    offset += blob.size;
  }
  for (const entry of entries) {
    if (names.has(entry.name.toLowerCase()))
      throw new Error("ZIP filenames collide.");
    names.add(entry.name.toLowerCase());
    const blob = entry.blob || (await getBlob(entry.key));
    if (!blob)
      throw new Error(
        `Saved file ${entry.name} is missing. Resume the batch to rebuild it.`,
      );
    const name = new TextEncoder().encode(entry.name);
    const crc = await checksum(blob);
    const start = offset;
    const [b, v] = header(30);
    v.setUint32(0, 0x04034b50, true);
    v.setUint16(4, 20, true);
    v.setUint16(6, 0x800, true);
    v.setUint16(12, 33, true);
    v.setUint32(14, crc, true);
    v.setUint32(18, blob.size, true);
    v.setUint32(22, blob.size, true);
    v.setUint16(26, name.length, true);
    await emit(new Blob([b, name]));
    let checked = 0;
    for (let n = 0; n < blob.size; n += 4 * 1024 ** 2) {
      const slice = blob.slice(n, n + 4 * 1024 ** 2);
      checked = crc32(new Uint8Array(await slice.arrayBuffer()), checked);
      await emit(slice);
    }
    if (checked !== crc) throw new Error("ZIP member CRC validation failed.");
    const [c, d] = header(46);
    d.setUint32(0, 0x02014b50, true);
    d.setUint16(4, 20, true);
    d.setUint16(6, 20, true);
    d.setUint16(8, 0x800, true);
    d.setUint16(14, 33, true);
    d.setUint32(16, crc, true);
    d.setUint32(20, blob.size, true);
    d.setUint32(24, blob.size, true);
    d.setUint16(28, name.length, true);
    d.setUint32(42, start, true);
    central.push(new Blob([c, name]));
  }
  const start = offset;
  await emit(new Blob(central));
  const centralSize = offset - start;
  const [b, v] = header(22);
  v.setUint32(0, 0x06054b50, true);
  v.setUint16(8, entries.length, true);
  v.setUint16(10, entries.length, true);
  v.setUint32(12, centralSize, true);
  v.setUint32(16, start, true);
  await emit(new Blob([b]));
  return { parts, size: offset, type: "application/zip" };
}
