/** Bounded header validation; no decoding and no user-controlled redirects. */
import {safePublicSourceUrl} from "./contributions";

export type MediaKind = "video" | "image";
export const maxBytes = (kind: MediaKind) => (kind === "video" ? 50 : 10) * 1024 * 1024;
export const PREFIX = 128 * 1024;
export const JPEG_HEADER_LIMIT = 2 * 1024 * 1024;
export const probeLimit = (kind: MediaKind) => kind === "image" ? JPEG_HEADER_LIMIT : PREFIX;

export function mediaUrl(raw: string, kind: MediaKind): string {
  return safePublicSourceUrl(raw, kind === "image" ? /\.(jpeg|jpg|png|webp)$/i : /\.(mp4|webm|mov)$/i);
}

/** SOI has already been consumed. Read only marker/length/SOF fields;
 * skip segment payloads rather than copying metadata into a parsing buffer.
 * The caller enforces a byte budget and distinguishes EOF from the budget.
 */
export async function scanJpeg(
  read: (count: number) => Promise<Uint8Array>,
  skip: (count: number) => Promise<void>,
): Promise<string> {
  for (let segments = 0; segments < 1024; segments++) {
    if ((await read(1))[0] !== 0xff) throw new TypeError("invalid_media_header");
    let marker = (await read(1))[0]!;
    let fill = 0;
    while (marker === 0xff) {
      if (++fill > 1024) throw new TypeError("jpeg_header_too_long");
      marker = (await read(1))[0]!;
    }
    if (marker === 0x01) continue; // TEM has no length.
    if (marker === 0x00 || marker === 0xd8 || marker === 0xd9 || marker === 0xda ||
        (marker >= 0xd0 && marker <= 0xd7)) throw new TypeError("invalid_media_header");
    const size = await read(2);
    const len = size[0]! * 256 + size[1]!;
    if (len < 2) throw new TypeError("invalid_media_header");
    if ([0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf].includes(marker)) {
      if (len < 8) throw new TypeError("invalid_media_header");
      const fields = await read(5);
      const height = fields[1]! * 256 + fields[2]!;
      const width = fields[3]! * 256 + fields[4]!;
      await skip(len - 7);
      if (!width || !height || width * height > 24_000_000) throw new TypeError("image_dimensions");
      return "image/jpeg";
    }
    await skip(len - 2);
  }
  throw new TypeError("jpeg_header_too_long");
}

export function inspectHeader(b: Uint8Array, kind: MediaKind): string {
  const v = new DataView(b.buffer, b.byteOffset, b.byteLength);
  const ascii = (p: number, n: number) => String.fromCharCode(...b.subarray(p, p+n));
  const u32 = (p: number) => v.getUint32(p);
  const u24le = (p: number) => b[p]! + b[p+1]!*256 + b[p+2]!*65536;
  const pixels = (w: number, h: number, mime: string) => {
    if (!w || !h || w*h > 24_000_000) throw new TypeError("image_dimensions");
    return mime;
  };
  if (kind === "image") {
    if (b.length >= 24 && u32(0) === 0x89504e47 && u32(4) === 0x0d0a1a0a &&
        u32(8) === 13 && ascii(12,4) === "IHDR") {
      return pixels(u32(16), u32(20), "image/png");
    }
    if (b.length >= 30 && ascii(0,4)==="RIFF" && ascii(8,4)==="WEBP") {
      if (ascii(12,4)==="VP8X") return pixels(1+u24le(24),1+u24le(27),"image/webp");
      if (ascii(12,4)==="VP8L" && b[20]===0x2f) {
        const bits=v.getUint32(21,true);
        return pixels(1+(bits&0x3fff),1+((bits>>>14)&0x3fff),"image/webp");
      }
      if (ascii(12,4)==="VP8 " && ascii(23,3)==="\x9d\x01\x2a") {
        return pixels(v.getUint16(26,true)&0x3fff,v.getUint16(28,true)&0x3fff,"image/webp");
      }
    }
    if (b[0]===0xff && b[1]===0xd8) {
      let p=2;
      while(p+4<=b.length && b[p]===0xff) {
        while(b[p]===0xff) p++;
        const marker=b[p++]!;
        if (marker===0xda || marker===0xd9 || p+2>b.length) break;
        const len=v.getUint16(p);
        if (len<2 || p+len>b.length) break;
        if ([0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf].includes(marker) && len>=8) {
          return pixels(v.getUint16(p+5),v.getUint16(p+3),"image/jpeg");
        }
        p+=len;
      }
    }
  } else {
    // ISO BMFF: require a recognizable file type, not just the extension.
    if (b.length>=16 && ascii(4,4)==="ftyp" && u32(0)>=16 && u32(0)<=b.length) {
      return ascii(8,4)==="qt  " ? "video/quicktime" : "video/mp4";
    }
    if (b.length>=8 && u32(0)===0x1a45dfa3) {
      // EBML header must declare the WebM document type.
      for(let i=4;i+7<=Math.min(b.length,4096);i++) {
        if(b[i]===0x42 && b[i+1]===0x82 && b[i+2]===0x84 && ascii(i+3,4)==="webm") return "video/webm";
      }
    }
  }
  throw new TypeError("invalid_media_header");
}

/** Return duration only when the bounded prefix contains a usable container header.
 * Unknown durations are not approved for render or indexing in this stage.
 */
export function videoDuration(b: Uint8Array, mime: string): number | null {
  const v=new DataView(b.buffer,b.byteOffset,b.byteLength);
  const ascii=(p:number)=>String.fromCharCode(...b.subarray(p,p+4));
  if(mime!=="video/webm") {
    for(let p=0;p+8<=b.length;) {
      const size=v.getUint32(p);
      if(size<8) break;
      if(ascii(p+4)==="moov") {
        for(let q=p+8;q+8<=Math.min(p+size,b.length);) {
          const n=v.getUint32(q);
          if(n<8 || q+n>p+size) break;
          if(ascii(q+4)==="mvhd") {
            const version=b[q+8];
            const off=q+(version===0?20:28);
            if((version!==0 && version!==1) || off+(version===0?8:12)>Math.min(q+n,b.length)) return null;
            const scale=v.getUint32(off);
            const duration=version===0?v.getUint32(off+4):Number(v.getBigUint64(off+4));
            return scale && duration>0 ? duration/scale : null;
          }
          q+=n;
        }
      }
      p+=size;
    }
  } else {
    // Matroska Info: parse bounded EBML elements, including unknown Segment size.
    function vint(p:number, id=false): [number,number] | null {
      const first=b[p];
      if(!first)return null;
      let len=1,mask=128;
      while(!(first&mask) && len<8){mask>>=1;len++;}
      if(p+len>b.length || (id && len>4))return null;
      let val=id?first:first&(mask-1);
      for(let i=1;i<len;i++)val=val*256+b[p+i]!;
      return [val,len];
    }
    function scan(start:number,end:number,depth:number):number|null {
      for(let p=start;p<end;) {
        const id=vint(p,true); if(!id)return null;
        const size=vint(p+id[1]);if(!size)return null;
        const at=p+id[1]+size[1],stop=Math.min(end,at+size[0]);
        if(id[0]===0x18538067 && depth===0)return scan(at,stop,1);
        if(id[0]===0x1549a966 && depth===1) {
          let scale=1000000,duration=0;
          for(let q=at;q<stop;) {
            const key=vint(q,true);if(!key)break;
            const len=vint(q+key[1]);if(!len)break;
            const pos=q+key[1]+len[1],n=len[0];
            if(pos+n>stop)break;
            if(key[0]===0x2ad7b1 && n<=6) {
              scale=0;for(let i=0;i<n;i++)scale=scale*256+b[pos+i]!;
            }
            if(key[0]===0x4489)duration=n===4?v.getFloat32(pos):n===8?v.getFloat64(pos):0;
            q=pos+n;
          }
          const seconds=duration*scale/1e9;
          return Number.isFinite(seconds)&&seconds>0?seconds:null;
        }
        if(at+size[0]>end)break;
        p=at+size[0];
      }
      return null;
    }
    return scan(0,b.length,0);
  }
  return null;
}
