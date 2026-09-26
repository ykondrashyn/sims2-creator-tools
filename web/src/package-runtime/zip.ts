// Lossless ZIP packing. Stream one member at a time and keep stored data when
// DEFLATE would be larger. Package bytes are never changed by archive packing.
import { Deflate, Inflate } from "fflate";
const CHUNK = 64 * 1024;
const STORAGE_CHUNK = 4 * 1024 ** 2;
const table = Uint32Array.from({ length: 256 }, (_, n) => {
  for (let i = 0; i < 8; i++) n = n & 1 ? 0xedb88320 ^ (n >>> 1) : n >>> 1;
  return n >>> 0;
});
export function crc32(bytes: Uint8Array<any>, crc = 0) {
  let c = crc ^ 0xffffffff;
  for (const b of bytes) c = table[(c ^ b) & 255] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function header(
  size: number,
): [Uint8Array<ArrayBuffer>, DataView<ArrayBuffer>] {
  const b = new Uint8Array(size);
  return [b, new DataView(b.buffer)];
}
async function checksum(blob: Blob, checkActive: () => void) {
  let crc = 0;
  for (let n = 0; n < blob.size; n += 4 * 1024 ** 2) {
    checkActive();
    const bytes = await blob.slice(n, n + 4 * 1024 ** 2).arrayBuffer();
    checkActive();
    crc = crc32(new Uint8Array(bytes), crc);
  }
  return crc;
}
export async function archive(
  job: { id: any },
  entries: string | any[],
  token: any,
  { getBlob, putBlob, deleteBlob, checkActive = () => {} }: any,
) {
  let offset = 0;
  const parts: string[] = [],
    central = [];
  const names = new Set();
  const temporary = new Set();
  const compression = {
    version: 1,
    algorithm: "deflate",
    level: 6,
    original_member_bytes: 0,
    stored_member_bytes: 0,
    compressed_members: 0,
    stored_members: 0,
  };
  let index = 0;
  async function emit(blob: Blob, materialized?: ArrayBuffer) {
    checkActive();
    if (offset + blob.size > 512 * 1024 ** 2)
      throw new Error(
        "Archive exceeds 512 MiB. Reduce the batch size or its textures.",
      );
    const key = `job:${job.id}:zip:${token}:${index++}`;
    // Materialize each bounded chunk before storing it. In Firefox, re-storing
    // a slice of an IndexedDB-backed Blob can retain the wrong backing range.
    const bytes = materialized ?? (await blob.arrayBuffer());
    checkActive();
    if (bytes.byteLength !== blob.size)
      throw new Error("ZIP chunk length changed before storage.");
    await putBlob(key, new Blob([bytes], { type: blob.type }), token);
    const stored = await getBlob(key);
    if (
      !stored ||
      stored.size !== blob.size ||
      (await checksum(stored, checkActive)) !== crc32(new Uint8Array(bytes))
    )
      throw new Error(
        "Stored ZIP chunk failed CRC verification. Resume the saved batch.",
      );
    parts.push(key);
    offset += blob.size;
  }
  async function removeTemporary(key: unknown) {
    await deleteBlob(key, token);
    temporary.delete(key);
  }
  async function prepare(
    blob: {
      size: number;
      slice: (
        arg0: number,
        arg1: number,
      ) => { (): any; new (): any; arrayBuffer: { (): any; new (): any } };
    },
    member: number,
  ) {
    let crc = 0,
      size = 0,
      pendingBytes = 0;
    const pending: BlobPart[] = [],
      chunks: any[] = [];
    const encoder = new Deflate({ level: 6 }, (bytes) => {
      if (bytes.length) {
        pending.push(bytes);
        pendingBytes += bytes.length;
      }
    });
    async function flush() {
      if (!pendingBytes) return;
      const value = new Blob(pending);
      // An input step is bounded, as is each emitted compressed storage chunk.
      for (let at = 0; at < value.size; at += STORAGE_CHUNK) {
        checkActive();
        const bytes = new Uint8Array(
          await value.slice(at, at + STORAGE_CHUNK).arrayBuffer(),
        );
        const key = `job:${job.id}:zip:${token}:deflate:${member}:${chunks.length}`;
        temporary.add(key);
        await putBlob(key, new Blob([bytes]), token);
        chunks.push({ key, size: bytes.length, crc: crc32(bytes) });
        size += bytes.length;
      }
      pending.length = 0;
      pendingBytes = 0;
    }
    if (!blob.size) encoder.push(new Uint8Array(), true);
    for (let at = 0; at < blob.size; at += CHUNK) {
      checkActive();
      const bytes = new Uint8Array(
        await blob.slice(at, at + CHUNK).arrayBuffer(),
      );
      crc = crc32(bytes, crc);
      encoder.push(bytes, at + CHUNK >= blob.size);
      if (pendingBytes >= STORAGE_CHUNK) await flush();
      // Let cancellation, the storage lease heartbeat and painting run even
      // when the source Blob is already in memory.
      if ((at + CHUNK) % (1024 * 1024) === 0)
        await new Promise((resolve) => setTimeout(resolve, 0));
    }
    await flush();
    if (size >= blob.size) {
      for (const { key } of chunks) await removeTemporary(key);
      return { crc, size: blob.size, method: 0, chunks: [] };
    }
    // Verify the actual stored candidate by inflating it in bounded steps.
    // Check both its compressed chunks and the original member CRC/length.
    let decodedSize = 0,
      decodedCrc = 0;
    const decoder = new Inflate((bytes) => {
      decodedSize += bytes.length;
      if (decodedSize > blob.size)
        throw new Error("ZIP member expands beyond its original size.");
      decodedCrc = crc32(bytes, decodedCrc);
    });
    for (let i = 0; i < chunks.length; i++) {
      const chunk = chunks[i],
        stored = await getBlob(chunk.key);
      if (!stored || stored.size !== chunk.size)
        throw new Error(
          "Stored compressed ZIP data failed CRC verification. Resume the saved batch.",
        );
      // Small DEFLATE input steps also bound decoder output for highly
      // repetitive files (each byte can expand by roughly 1,032 bytes).
      let compressedCrc = 0;
      for (let at = 0; at < stored.size; at += 4096) {
        checkActive();
        const bytes = new Uint8Array(
          await stored.slice(at, at + 4096).arrayBuffer(),
        );
        compressedCrc = crc32(bytes, compressedCrc);
        decoder.push(
          bytes,
          i === chunks.length - 1 && at + 4096 >= stored.size,
        );
      }
      if (compressedCrc !== chunk.crc)
        throw new Error(
          "Stored compressed ZIP data failed CRC verification. Resume the saved batch.",
        );
    }
    if (decodedSize !== blob.size || decodedCrc !== crc)
      throw new Error("Compressed ZIP member failed round-trip validation.");
    return { crc, size, method: 8, chunks };
  }
  if (typeof deleteBlob !== "function")
    throw new Error("Reload the website to use this saved archive runtime.");
  try {
    for (const entry of entries) {
      checkActive();
      if (names.has(entry.name.toLowerCase()))
        throw new Error("ZIP filenames collide.");
      names.add(entry.name.toLowerCase());
      const blob = entry.blob || (await getBlob(entry.key));
      if (!blob)
        throw new Error(
          `Saved file ${entry.name} is missing. Resume the batch to rebuild it.`,
        );
      const name = new TextEncoder().encode(entry.name);
      const packed = await prepare(blob, names.size),
        { crc } = packed;
      compression.original_member_bytes += blob.size;
      compression.stored_member_bytes += packed.size;
      compression[
        packed.method === 8 ? "compressed_members" : "stored_members"
      ]++;
      const start = offset;
      const [b, v] = header(30);
      v.setUint32(0, 0x04034b50, true);
      v.setUint16(4, 20, true);
      v.setUint16(6, 0x800, true);
      v.setUint16(8, packed.method, true);
      v.setUint16(12, 33, true);
      v.setUint32(14, crc, true);
      v.setUint32(18, packed.size, true);
      v.setUint32(22, blob.size, true);
      v.setUint16(26, name.length, true);
      await emit(new Blob([b, name]));
      if (packed.method === 8) {
        for (const chunk of packed.chunks) {
          const stored = await getBlob(chunk.key);
          const bytes = stored && (await stored.arrayBuffer());
          if (
            !stored ||
            !bytes ||
            stored.size !== chunk.size ||
            crc32(new Uint8Array(bytes)) !== chunk.crc
          )
            throw new Error(
              "Stored compressed ZIP data changed. Resume the saved batch.",
            );
          await emit(stored, bytes);
          await removeTemporary(chunk.key);
        }
      } else {
        let checked = 0;
        for (let n = 0; n < blob.size; n += STORAGE_CHUNK) {
          const slice = blob.slice(n, n + STORAGE_CHUNK);
          const bytes = await slice.arrayBuffer();
          checked = crc32(new Uint8Array(bytes), checked);
          await emit(slice, bytes);
        }
        if (checked !== crc)
          throw new Error("ZIP member CRC validation failed.");
      }
      const [c, d] = header(46);
      d.setUint32(0, 0x02014b50, true);
      d.setUint16(4, 20, true);
      d.setUint16(6, 20, true);
      d.setUint16(8, 0x800, true);
      d.setUint16(10, packed.method, true);
      d.setUint16(14, 33, true);
      d.setUint32(16, crc, true);
      d.setUint32(20, packed.size, true);
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
    return { parts, size: offset, type: "application/zip", compression };
  } finally {
    // Interrupted attempts leave the original inputs and validated package
    // checkpoints intact. The regular cleanup also removes fenced leftovers.
    for (const key of temporary) await removeTemporary(key).catch(() => {});
  }
}
