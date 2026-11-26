/** Range-based ZIP member streaming. Only bounded metadata is buffered. */
import {MAX_ARCHIVE, zipEntry} from "./video-zip";

const MAX_BUFFER = 8_000_000;
const MAX_VIDEO = 100_000_000;
const TAIL = 65557;
export class ArtifactExpired extends Error {}

function checkExpired(response:Response) {
  if (response.status === 410) throw new ArtifactExpired("artifact expired");
}
async function bounded(response:Response, limit:number) {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("missing response body");
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const {done,value} = await reader.read();
      if (done) break;
      size += value.length;
      if (size > limit) throw new Error("body exceeds limit");
      chunks.push(value);
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk,offset); offset += chunk.length; }
  return bytes.buffer;
}
function bounds(response:Response) {
  checkExpired(response);
  const match = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(response.headers.get("content-range") ?? "");
  if (response.status !== 206 || !match) throw new Error("invalid upstream range");
  const [start,end,total] = match.slice(1).map(Number) as [number,number,number];
  if (![start,end,total].every(Number.isSafeInteger) || start < 0 ||
      end < start || end >= total || total > MAX_ARCHIVE) throw new Error("invalid ZIP size");
  const length = response.headers.get("content-length");
  if (length !== null && Number(length) !== end-start+1) throw new Error("wrong range length");
  return {start,end,total};
}
export async function artifactResponse(url:string, range?:string,fetcher:typeof fetch=fetch) {
  const response = await fetcher(url,{redirect:"manual",headers:{
    ...(range ? {range} : {}), "accept-encoding":"identity",
  }});
  checkExpired(response);
  return response;
}
async function exact(url:string,start:number,end:number,total:number,limit:number) {
  if (start < 0 || end >= total || end < start || end-start+1 > limit)
    throw new Error("invalid metadata range");
  const response = await artifactResponse(url,"bytes="+start+"-"+end);
  const b = bounds(response);
  if (b.start!==start || b.end!==end || b.total!==total) {
    await response.body?.cancel();
    throw new Error("wrong upstream range");
  }
  const data = await bounded(response,limit);
  if (data.byteLength!==end-start+1) throw new Error("truncated range");
  return new DataView(data);
}
type Member = {offset:number;size:number;packed:number;method:number};
async function locate(url:string,tail:DataView,start:number,total:number):Promise<Member> {
  let end = -1;
  for(let n=tail.byteLength-22;n>=0;n--) {
    if(tail.getUint32(n,true)===0x06054b50 &&
        n+22+tail.getUint16(n+20,true)===tail.byteLength) {end=n;break;}
  }
  if(end<0 || tail.getUint16(end+4,true) || tail.getUint16(end+6,true))
    throw new Error("invalid zip directory");
  const count=tail.getUint16(end+10,true), length=tail.getUint32(end+12,true);
  const central=tail.getUint32(end+16,true);
  if(!count || count>32 || tail.getUint16(end+8,true)!==count ||
      central+length!==start+end || length>1_000_000 || length<46)
    throw new Error("invalid central directory");
  const dir=central>=start ? new DataView(tail.buffer,central-start,length) :
    await exact(url,central,central+length-1,total,1_000_000);
  let pos=0, found:Member|undefined;
  const decoder=new TextDecoder();
  for(let n=0;n<count;n++) {
    if(pos+46>length || dir.getUint32(pos,true)!==0x02014b50)
      throw new Error("invalid central entry");
    const flags=dir.getUint16(pos+8,true),method=dir.getUint16(pos+10,true);
    const packed=dir.getUint32(pos+20,true),size=dir.getUint32(pos+24,true);
    const nameLen=dir.getUint16(pos+28,true),extra=dir.getUint16(pos+30,true);
    const comment=dir.getUint16(pos+32,true),local=dir.getUint32(pos+42,true);
    const next=pos+46+nameLen+extra+comment;
    if(next>length) throw new Error("invalid central length");
    const name=decoder.decode(new Uint8Array(dir.buffer,dir.byteOffset+pos+46,nameLen));
    if(name==="video.mp4") {
      if(found || flags&1 || (method!==0 && method!==8) || size<1 ||
          size>MAX_VIDEO || local+30>central || (method===0 && packed!==size))
        throw new Error("invalid member");
      const head=await exact(url,local,local+29,total,30);
      if(head.getUint32(0,true)!==0x04034b50 || head.getUint16(6,true)!==flags ||
          head.getUint16(8,true)!==method || head.getUint16(26,true)!==nameLen)
        throw new Error("invalid local header");
      const offset=local+30+nameLen+head.getUint16(28,true);
      if(offset+packed>central) throw new Error("invalid member bounds");
      const actual=await exact(url,local+30,local+29+nameLen,total,65535);
      if(decoder.decode(actual)!==name) throw new Error("mismatched local name");
      found={offset,size,packed,method};
    }
    pos=next;
  }
  if(!found || pos!==length) throw new Error("missing zip member");
  return found;
}
function clientRange(value:string|null,size:number):[number,number]|null {
  if(value===null) return [0,size-1];
  const m=/^bytes=(\d*)-(\d*)$/.exec(value);
  if(!m || (!m[1]&&!m[2])) return null;
  const a=m[1]?Number(m[1]):Math.max(0,size-Number(m[2]));
  const b=m[1]&&m[2]?Math.min(size-1,Number(m[2])):size-1;
  return Number.isSafeInteger(a)&&Number.isSafeInteger(b)&&a>=0&&a<=b&&a<size
    ? [a,b] : null;
}
function headers(size:number,range:string|null,part:[number,number]|null) {
  const out=new Headers({"content-type":"video/mp4","cache-control":"no-store",
    "referrer-policy":"no-referrer","content-disposition":'attachment; filename="video.mp4"',
    "accept-ranges":"bytes"});
  if(!part) out.set("content-range","bytes */"+size);
  else {
    out.set("content-length",String(part[1]-part[0]+1));
    if(range!==null) out.set("content-range","bytes "+part[0]+"-"+part[1]+"/"+size);
  }
  return out;
}
async function buffered(response:Response,range:string|null) {
  const data=await zipEntry(await bounded(response,MAX_BUFFER),"video.mp4",MAX_BUFFER);
  const part=clientRange(range,data.length), h=headers(data.length,range,part);
  if(!part) return new Response(null,{status:416,headers:h});
  return new Response(data.subarray(part[0],part[1]+1),{status:range===null?200:206,headers:h});
}
export async function serveVideo(url:string,range:string|null):Promise<Response> {
  const tailResponse=await artifactResponse(url,"bytes=-"+TAIL);
  // Some providers ignore Range. Never buffer an unbounded video in that case.
  if(tailResponse.status===200) {
    const size=Number(tailResponse.headers.get("content-length"));
    if(!Number.isSafeInteger(size)||size<1||size>MAX_BUFFER) {
      await tailResponse.body?.cancel();
      throw new Error("upstream range unsupported");
    }
    return buffered(tailResponse,range);
  }
  const b=bounds(tailResponse);
  if(b.end!==b.total-1 || b.start!==Math.max(0,b.total-TAIL)) {
    await tailResponse.body?.cancel();
    throw new Error("incorrect suffix range");
  }
  const tail=await bounded(tailResponse,TAIL);
  if(tail.byteLength!==b.end-b.start+1) throw new Error("truncated suffix");
  const member=await locate(url,new DataView(tail),b.start,b.total);
  if(member.method===8) {
    if(member.size>MAX_BUFFER||member.packed>MAX_BUFFER||b.total>MAX_BUFFER)
      throw new Error("deflate exceeds bounded fallback");
    const response=await artifactResponse(url);
    if(!response.ok) throw new Error("artifact unavailable");
    return buffered(response,range);
  }
  const part=clientRange(range,member.size),h=headers(member.size,range,part);
  if(!part) return new Response(null,{status:416,headers:h});
  const start=member.offset+part[0],end=member.offset+part[1];
  const response=await artifactResponse(url,"bytes="+start+"-"+end);
  const actual=bounds(response);
  if(actual.start!==start||actual.end!==end||actual.total!==b.total||!response.body) {
    await response.body?.cancel();
    throw new Error("wrong video range");
  }
  let remaining=end-start+1;
  const stream=response.body.pipeThrough(new TransformStream<Uint8Array,Uint8Array>({
    transform(chunk,controller) {
      remaining-=chunk.length;
      if(remaining<0) throw new Error("oversized video range");
      controller.enqueue(chunk);
    },
    flush() {if(remaining!==0) throw new Error("truncated video range");},
  }));
  return new Response(stream,{status:range===null?200:206,headers:h});
}
