/** Bounded ZIP reader for Actions artifacts; no ZIP dependency or filesystem extraction. */
const MAX_VIDEO = 100_000_000;
export const MAX_ARCHIVE = MAX_VIDEO + 1_000_000;

export async function zipEntry(
  buffer: ArrayBuffer, wanted: string, limit: number,
): Promise<Uint8Array<ArrayBuffer>> {
  const view = new DataView(buffer);
  const bytes = new Uint8Array(buffer);
  let end = -1;
  for (let i = buffer.byteLength - 22; i >= Math.max(0, buffer.byteLength - 65557); i--) {
    if (view.getUint32(i, true) === 0x06054b50 &&
        i + 22 + view.getUint16(i + 20, true) === buffer.byteLength) {
      end = i;
      break;
    }
  }
  if (end < 0 || view.getUint16(end + 4, true) || view.getUint16(end + 6, true)) {
    throw new Error("invalid zip directory");
  }
  const count = view.getUint16(end + 10, true);
  const size = view.getUint32(end + 12, true);
  let offset = view.getUint32(end + 16, true);
  const directoryEnd = offset + size;
  if (!count || count > 32 || directoryEnd !== end ||
      view.getUint16(end + 8, true) !== count) throw new Error("invalid zip entries");
  let found: {start:number; packed:number; unpacked:number; method:number} | undefined;
  for (let i = 0; i < count; i++) {
    if (offset + 46 > directoryEnd || view.getUint32(offset, true) !== 0x02014b50) {
      throw new Error("invalid central entry");
    }
    const flags = view.getUint16(offset + 8, true);
    const method = view.getUint16(offset + 10, true);
    const packed = view.getUint32(offset + 20, true);
    const unpacked = view.getUint32(offset + 24, true);
    const nameLength = view.getUint16(offset + 28, true);
    const extra = view.getUint16(offset + 30, true);
    const comment = view.getUint16(offset + 32, true);
    const local = view.getUint32(offset + 42, true);
    const next = offset + 46 + nameLength + extra + comment;
    if (next > directoryEnd) throw new Error("invalid entry length");
    const name = new TextDecoder().decode(bytes.subarray(offset + 46, offset + 46 + nameLength));
    if (name === wanted) {
      if (found || flags & 1 || (method !== 0 && method !== 8) ||
          unpacked > limit || !unpacked || local + 30 > end ||
          view.getUint32(local, true) !== 0x04034b50 ||
          view.getUint16(local + 8, true) !== method ||
          view.getUint16(local + 6, true) !== flags) throw new Error("invalid zip member");
      const nameLen = view.getUint16(local + 26, true);
      const start = local + 30 + nameLen + view.getUint16(local + 28, true);
      if (start + packed > view.getUint32(end + 16, true) ||
          new TextDecoder().decode(bytes.subarray(local + 30, local + 30 + nameLen)) !== name ||
          (method === 0 && packed !== unpacked)) throw new Error("invalid member bounds");
      found = {start, packed, unpacked, method};
    }
    offset = next;
  }
  if (!found || offset !== directoryEnd) throw new Error("missing zip member");
  const compressed = bytes.subarray(found.start, found.start + found.packed);
  if (found.method === 0) return compressed;
  const output = new Uint8Array(found.unpacked);
  const reader = new Blob([compressed]).stream()
    .pipeThrough(new DecompressionStream("deflate-raw")).getReader();
  let length = 0;
  try {
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      if (length + value.length > output.length) throw new Error("oversized zip member");
      output.set(value, length);
      length += value.length;
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
  if (length !== output.length) throw new Error("invalid decompressed length");
  return output;
}
