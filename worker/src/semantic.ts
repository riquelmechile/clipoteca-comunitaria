/** M22 r2: explicitly gated, budgeted multilingual CLIP search. No Torch in Worker. */

import { loadCatalog, searchTokens, type CatalogEnv, type Clip } from "./catalog";
import { type DispatchEnv } from "./dispatch";
import { quota } from "./quota";
import casefoldMap from "./casefold-map.json";

const DIM = 512;
const WIDTH = DIM * 2;
const SEAL_CAPACITY = 256;
const SHARD_CAPACITY = 4096;
const MAX_SHARDS = 500; // equal to Python; corpus CPU budget is checked before fetching
const MAX_MANIFEST_BYTES = 1_000_000;
const DEFAULT_CLIPS_BUDGET = 256;
const PAID_CLIPS_BUDGET = 4096;
export const SEMANTIC_TAU = 0.20;
const MAX_SEMANTIC_RESULTS = 50;
const RELATIVE_COSINE_MARGIN = 0.05;
const GLOBAL_QUERIES_PER_HOUR = 20;
const QUERY_COOLDOWN_SECONDS = 600;
const ROOT = "https://raw.githubusercontent.com/";
const REPO = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const SEALED_FILE = /^shards\/shard-[0-9]{6}-[a-f0-9]{64}\.f16$/;
const SEALED_IDS = /^ids\/shard-[0-9]{6}-[a-f0-9]{64}\.json$/;
const ID = /^clip-[0-9]{6}$/;
const DIGEST = /^[a-f0-9]{64}$/;

export interface SemanticEnv extends CatalogEnv, DispatchEnv {
  QUERY_ENCODER_URL?: string;
  CLIPOTECA_QUERY_DISPATCH_MODE?: string;
  SEMANTIC_SEARCH?: string;
  SEMANTIC_MAX_CLIPS?: string;
  SEMANTIC_MIN_COSINE?: string;
  QUERY_ENCODER_HOSTS?: string;
}
export type SemanticStatus = "ready" | "pending" | "unavailable";
export interface SemanticSearchResult {
  clips: Clip[];
  next_cursor: string | null;
  semantic: SemanticStatus;
  scores?: Record<string, number>;
  semantic_reason?: string;
}

interface Shard {
  path: string;
  ids_path: string;
  count: number;
  sha256: string;
  blob_sha: string;
  ids_sha256: string;
  ids_blob_sha: string;
}
interface Manifest {
  version: number;
  dimension: number;
  dtype: string;
  shard_capacity: number;
  seal_capacity: number;
  shards: Shard[];
  tail: Shard | null;
}
interface PackedCorpus {
  ids: string[];
  vectors: Float32Array;
  reason?: string;
}
class SemanticFault extends Error {
  constructor(public readonly code: string) { super(code); }
}
const memoryCache = new Map<string, {until: number; value: Uint8Array}>();
const missingAssets = new Map<string, number>();
const corpusCache = new Map<string, {until: number; data: PackedCorpus}>();
const encoderMemory = new Map<string, {until: number; vector: Float32Array}>();
const queryDispatch = new Map<string, {until: number; pending: boolean}>();
export function resetSemanticCache(): void {
  memoryCache.clear();
  missingAssets.clear();
  corpusCache.clear();
  encoderMemory.clear();
}
export function resetQueryDispatch(): void {
  queryDispatch.clear();
  resetDispatchReservations();
}

function boundedPut<K,V>(map: Map<K,V>, key: K, value: V, max = 16): void {
  if (map.size >= max) map.delete(map.keys().next().value!);
  map.set(key, value);
}
function repository(env: SemanticEnv): string {
  const name = env.CLIPOTECA_GITHUB_REPO ?? "riquelmechile/clipoteca-comunitaria";
  if (!REPO.test(name)) throw new SemanticFault("invalid_repository");
  return name;
}
function asset(env: SemanticEnv, relative: string): string {
  return ROOT + repository(env) + "/main/data/embeddings/" + relative;
}

// Generated from Python Unicode 17.0.0 casefold table (1530+ mappings).
// Apply NFKC first, then the *complete* Python casefold mapping; JS
// toLowerCase differs on Greek, Cherokee and other codepoints.
const CASEFOLD = new Map<string,string>();
for (const pair of casefoldMap.pairs.split(";")) {
  const [source,target] = pair.split(":");
  if(!source || !target)continue;
  CASEFOLD.set(String.fromCodePoint(parseInt(source,16)),
    target.split(",").map(code=>String.fromCodePoint(parseInt(code,16))).join(""));
}
export function normalizedQuery(query: string): string {
  const fold = Array.from(query.normalize("NFKC"), char => CASEFOLD.get(char) ?? char).join("");
  return fold.replace(/[\s\u0085\u001c-\u001f]+/gu, " ").trim();
}

export async function sha256(data: Uint8Array | string): Promise<string> {
  const input = typeof data === "string" ? new TextEncoder().encode(data) : data;
  const digest = await crypto.subtle.digest("SHA-256", Uint8Array.from(input));
  return [...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2, "0")).join("");
}
// Build the table once per isolate, never reconstruct 512 float16 values
// from math-heavy exponent operations for each vector on each invocation.
const HALF_TABLE = (() => {
  const out = new Float32Array(65536);
  for (let bits=0;bits<65536;bits++) {
    const sign=bits&0x8000?-1:1, exponent=(bits>>>10)&0x1f, mantissa=bits&0x3ff;
    out[bits] = exponent===31 ? (mantissa?NaN:sign*Infinity)
      : exponent===0 ? sign*mantissa*2**-24
      : sign*(1+mantissa/1024)*2**(exponent-15);
  }
  return out;
})();
export function halfFloat(bits: number): number {
  return HALF_TABLE[bits & 0xffff]!;
}

export function decodeFloat16(raw: Uint8Array, offset = 0): Float32Array {
  if (offset < 0 || offset + WIDTH > raw.length) throw new SemanticFault("shard_corrupt");
  const out = new Float32Array(DIM);
  let norm = 0;
  for(let i=0;i<DIM;i++){
    const n = halfFloat(raw[offset + i * 2]! | (raw[offset + i * 2 + 1]! << 8));
    if (!Number.isFinite(n)) throw new SemanticFault("shard_corrupt");
    out[i] = n; norm += n*n;
  }
  if (norm < 0.98*0.98 || norm > 1.02*1.02) throw new SemanticFault("shard_corrupt");
  const len = Math.sqrt(norm);
  for(let i=0;i<DIM;i++) out[i] = out[i]! / len;
  return out;
}
export function normalizeEmbedding(input: unknown): Float32Array {
  if (!Array.isArray(input) || input.length !== DIM) throw new Error("invalid vector length");
  const vector = new Float32Array(DIM);
  let norm=0;
  for(let i=0;i<DIM;i++){
    if (typeof input[i] !== "number" || !Number.isFinite(input[i])) throw new Error("invalid vector element");
    vector[i] = input[i] as number;
    norm += vector[i]! * vector[i]!;
  }
  if (norm <= 1e-10 || !Number.isFinite(norm)) throw new Error("invalid vector norm");
  const len=Math.sqrt(norm);
  for(let i=0;i<DIM;i++) vector[i] = vector[i]! / len;
  return vector;
}
export function cosine(a: Float32Array, b: Float32Array): number {
  if(a.length !== DIM || b.length !== DIM) throw new Error("invalid vector dimension");
  let total=0;
  for(let i=0;i<DIM;i++) total += a[i]! * b[i]!;
  return Math.max(-1,Math.min(1,total));
}
export function semanticThreshold(env: SemanticEnv): number {
  const raw=env.SEMANTIC_MIN_COSINE;
  if(raw===undefined)return SEMANTIC_TAU;
  const value=Number(raw);
  return raw.trim()!=="" && Number.isFinite(value) && value>=-1 && value<=1
    ? value : SEMANTIC_TAU;
}
export function selectSemanticMatches(
  values: {id:string;cosine:number}[], absolute=SEMANTIC_TAU, topK=MAX_SEMANTIC_RESULTS,
): {id:string;cosine:number}[] {
  const valid=values.filter(x=>Number.isFinite(x.cosine));
  if(!valid.length)return [];
  valid.sort((a,b)=>b.cosine-a.cosine||a.id.localeCompare(b.id));
  const minimum=Math.max(absolute,valid[0]!.cosine-RELATIVE_COSINE_MARGIN);
  return valid.filter(row=>row.cosine+1e-6>=minimum).slice(0,topK);
}
export function rrfFuse(
  lexical: string[], semantic: {id: string; cosine: number}[], k=60,
): {id:string;combined_score:number}[] {
  const scores = new Map<string,number>();
  lexical.forEach((id, index)=>scores.set(id,(scores.get(id)??0)+1/(k+index+1)));
  semantic.forEach((row,index)=>scores.set(row.id,(scores.get(row.id)??0)+1/(k+index+1)));
  const order=new Map([...lexical,...semantic.map(x=>x.id)].map((id,index)=>[id,index]));
  return [...scores].map(([id,combined_score])=>({id,combined_score}))
    .sort((a,b)=>b.combined_score-a.combined_score ||
      (order.get(a.id)??Infinity)-(order.get(b.id)??Infinity) ||
      a.id.localeCompare(b.id));
}

function cacheDefault(): Cache | undefined {
  try { return (caches as CacheStorage & {default?: Cache}).default; }
  catch { return undefined; }
}
async function boundedBody(response: Response, cap: number): Promise<Uint8Array> {
  const declared=response.headers.get("content-length");
  if (declared && Number(declared)>cap) throw new SemanticFault("manifest_too_large");
  const reader=response.body?.getReader();
  if (!reader) throw new Error("response_has_no_body");
  const chunks:Uint8Array[]=[];
  let total=0;
  try {
    while(true){
      const {done,value}=await reader.read();
      if(done)break;
      if(value){
        total+=value.byteLength;
        if(total>cap){await reader.cancel();throw new SemanticFault("manifest_too_large");}
        chunks.push(value);
      }
    }
  }finally{try{reader.releaseLock();}catch{/* cancelled */}}
  const raw=new Uint8Array(total);let offset=0;
  for(const chunk of chunks){raw.set(chunk,offset);offset+=chunk.length;}
  return raw;
}
async function bytes(url:string, cap:number, ttl:number):Promise<Uint8Array|null>{
  const now=Date.now(), old=memoryCache.get(url);
  if(old && old.until>now)return old.value;
  if((missingAssets.get(url)??0)>now)return null;
  const cache=cacheDefault(), request=new Request(url);
  let response=cache&&ttl>0 ? await cache.match(request):undefined;
  if(!response){
    response=await fetch(url,{redirect:"error",headers:{
      accept:"application/octet-stream","user-agent":"clipoteca-worker",
    }});
    if(response.status===404){
      boundedPut(missingAssets,url,now+Math.max(1,ttl)*1000,16);
      return null;
    }
    if(!response.ok)throw new Error("semantic_asset_unavailable");
  }
  const raw=await boundedBody(response,cap);
  boundedPut(memoryCache,url,{until:now+ttl*1000,value:raw},16);
  if(cache&&ttl>0){
    try{
      await cache.put(request,new Response(Uint8Array.from(raw).buffer,{
        headers:{"cache-control":"public, max-age="+ttl},
      }));
    }catch{/* isolate memory cache remains */ }
  }
  return raw;
}

function validEntry(entry:unknown,index:number,sealed:boolean):entry is Shard {
  if(!entry || typeof entry!=="object" || Array.isArray(entry))return false;
  const s=entry as Shard;
  if(!Number.isInteger(s.count)||s.count<1||s.count>SEAL_CAPACITY||
    (sealed && s.count!==SEAL_CAPACITY) ||
    ![s.sha256,s.ids_sha256].every(x=>typeof x==="string"&&DIGEST.test(x)) ||
    ![s.blob_sha,s.ids_blob_sha].every(x=>typeof x==="string"&&/^[0-9a-f]{40}$/.test(x)))
    return false;
  if(!sealed)return (
    /^shards\/tail-[0-9a-f]{64}\.f16$/.test(s.path) &&
    /^ids\/tail-[0-9a-f]{64}\.json$/.test(s.ids_path) &&
    s.path.endsWith(s.sha256+".f16") &&
    s.ids_path.endsWith(s.ids_sha256+".json")
  );
  return SEALED_FILE.test(s.path)&&SEALED_IDS.test(s.ids_path)&&
    s.path.startsWith("shards/shard-"+String(index).padStart(6,"0")+"-")&&
    s.path.endsWith(s.sha256+".f16") &&
    s.ids_path.startsWith("ids/shard-"+String(index).padStart(6,"0")+"-")&&
    s.ids_path.endsWith(s.ids_sha256+".json");
}
function validateManifest(raw:unknown):Manifest{
  if(!raw||typeof raw!=="object"||Array.isArray(raw))throw new SemanticFault("manifest_invalid");
  const m=raw as Manifest;
  if(m.version!==3||m.dimension!==DIM||m.dtype!=="float16-le" ||
    m.shard_capacity!==SHARD_CAPACITY||m.seal_capacity!==SEAL_CAPACITY ||
    !Array.isArray(m.shards)||m.shards.length>MAX_SHARDS ||
    m.shards.some((s,i)=>!validEntry(s,i,true)) ||
    (m.tail!==null&&!validEntry(m.tail,m.shards.length,false)))
    throw new SemanticFault("manifest_invalid");
  return m;
}
function budget(env:SemanticEnv):number{
  const value=Number(env.SEMANTIC_MAX_CLIPS??DEFAULT_CLIPS_BUDGET);
  if(!Number.isSafeInteger(value)||value<1)return DEFAULT_CLIPS_BUDGET;
  return Math.min(PAID_CLIPS_BUDGET,value);
}

async function fetchManifest(env:SemanticEnv):Promise<{manifest:Manifest|null;hash:string}>{
  const raw=await bytes(asset(env,"manifest.json"),MAX_MANIFEST_BYTES,10);
  if(!raw)return {manifest:null,hash:"legacy"};
  return {manifest:validateManifest(JSON.parse(new TextDecoder().decode(raw))),hash:await sha256(raw)};
}
async function loadLegacy(env:SemanticEnv,maxClips:number):Promise<PackedCorpus>{
  const raw=await bytes(asset(env,"index.json"),MAX_MANIFEST_BYTES,10);
  if(!raw)return {ids:[],vectors:new Float32Array(0)};
  const doc=JSON.parse(new TextDecoder().decode(raw)) as {
    dimension?:number;dtype?:string;vectors?:Record<string,{path:string;sha256:string}>;
  };
  if(doc.dimension!==DIM||doc.dtype!=="float16-le"||!doc.vectors)
    throw new SemanticFault("legacy_index_invalid");
  const rows=Object.entries(doc.vectors);
  if(rows.length>35||rows.length>maxClips)throw new SemanticFault("cpu_budget_exceeded");
  const data=new Float32Array(rows.length*DIM),ids:string[]=[];
  for(const [id,meta] of rows){
    if(!ID.test(id)||meta.path!==id+".f16"||!DIGEST.test(meta.sha256))
      throw new SemanticFault("legacy_index_invalid");
    const rawVector=await bytes(asset(env,id+".f16"),WIDTH,60);
    if(!rawVector||await sha256(rawVector)!==meta.sha256)
      throw new SemanticFault("shard_corrupt");
    data.set(decodeFloat16(rawVector),ids.length*DIM);
    ids.push(id);
  }
  return {ids,vectors:data};
}
async function readShard(env:SemanticEnv,entry:Shard):Promise<{ids:string[];data:Uint8Array}>{
  // All shard paths, including tail, are now immutable SHA-addressed files.
  const ttl=3600;
  const [rawIds,rawBlob]=await Promise.all([
    bytes(asset(env,entry.ids_path),SEAL_CAPACITY*18,ttl),
    bytes(asset(env,entry.path),SEAL_CAPACITY*WIDTH,ttl),
  ]);
  if(!rawIds||!rawBlob||rawBlob.length!==entry.count*WIDTH ||
    await sha256(rawIds)!==entry.ids_sha256 ||
    await sha256(rawBlob)!==entry.sha256)throw new SemanticFault("shard_corrupt");
  const ids=JSON.parse(new TextDecoder().decode(rawIds)) as unknown;
  if(!Array.isArray(ids)||ids.length!==entry.count||
    ids.some(id=>typeof id!=="string"||!ID.test(id)) ||
    new Set(ids).size!==ids.length)throw new SemanticFault("shard_corrupt");
  return {ids,data:rawBlob};
}
async function loadPackedCorpus(env:SemanticEnv):Promise<PackedCorpus>{
  const {manifest,hash}=await fetchManifest(env);
  const key=repository(env)+":"+hash+":"+budget(env);
  const existing=corpusCache.get(key);
  if(existing&&existing.until>Date.now())return existing.data;
  const limit=budget(env);
  let corpus:PackedCorpus;
  if(!manifest){
    corpus=await loadLegacy(env,limit);
  }else{
    const shards=[...manifest.shards,...(manifest.tail?[manifest.tail]:[])];
    const total=shards.reduce((n,s)=>n+s.count,0);
    if(total>limit)throw new SemanticFault("cpu_budget_exceeded");
    const ids:string[]=[];
    const seen=new Set<string>();
    const vectors=new Float32Array(total*DIM);
    let corrupt=false;
    for(const entry of shards){
      try{
        const item=await readShard(env,entry);
        const decoded:Float32Array[]=[];
        for(let index=0;index<item.ids.length;index++){
          const id=item.ids[index]!;
          if(seen.has(id))throw new SemanticFault("shard_corrupt");
          decoded.push(decodeFloat16(item.data,index*WIDTH));
        }
        // Commit only after *all* vectors validate: a corrupt shard must
        // not leak partially decoded clip rows into the search results.
        for(let index=0;index<item.ids.length;index++){
          const id=item.ids[index]!;
          vectors.set(decoded[index]!,ids.length*DIM);
          seen.add(id);
          ids.push(id);
        }
      }catch{corrupt=true;} // one damaged shard does not suppress valid peers
    }
    corpus={ids,vectors:vectors.subarray(0,ids.length*DIM),
      ...(corrupt?{reason:"shard_corrupt"}:{})};
  }
  // A transient shard fetch error or empty corpus must not be sticky for 20m.
  const corpusTtl = !corpus.reason && corpus.ids.length > 0 ? 1_200_000 : 60_000;
  boundedPut(corpusCache,key,{until:Date.now()+corpusTtl,data:corpus},3);
  return corpus;
}
export async function loadCorpus(env:SemanticEnv):Promise<Map<string,Float32Array>>{
  const corpus=await loadPackedCorpus(env);
  return new Map(corpus.ids.map((id,index)=>[
    id,corpus.vectors.subarray(index*DIM,(index+1)*DIM),
  ]));
}

function encoderUrl(value:string, allowlist?:string):URL|null{
  try{
    const url=new URL(value),host=url.hostname.toLowerCase().replace(/\.+$/u,"");
    const configured=(allowlist??"").split(",").map(x=>x.trim().toLowerCase().replace(/\.+$/u,"")).filter(Boolean);
    if(configured.length&& !configured.includes(host))return null;
    if(url.protocol!=="https:"||url.username||url.password||url.hash||
      (url.port&&url.port!=="443")||host.includes(":")||host.includes("[")||
      !/^[a-z0-9.-]+$/.test(host)||!host.includes(".")||
      host==="localhost"||[".localhost",".local",".internal",".test",".lan",".corp",".home.arpa",".nip.io",".sslip.io"].some(s=>host.endsWith(s))||
      ["nip.io","sslip.io"].includes(host)||
      /^(?:\d+\.){3}\d+$/.test(host)||/^\d+[.\d]*$/.test(host))
      return null;
    return url;
  }catch{return null;}
}
type Gate = {allowed:boolean;pending:boolean};
function cacheRequest(suffix:string):Request{
  return new Request("https://clipoteca.invalid/_query-embed/"+suffix);
}
// Reservations are taken synchronously, before the FIRST await. Cache API
// has no atomic compare-and-swap, so it cannot enforce a global cross-colo
// quota. Within one isolate 50 simultaneous requests are serialized here.
const gateDigests = new Set<string>();
const gateIps = new Set<string>();
let gateInFlight=0;
let gateHour=-1;
let gateHourlyCommitted=0;
export function resetDispatchReservations():void{
  gateDigests.clear();gateIps.clear();gateInFlight=0;gateHour=-1;gateHourlyCommitted=0;
}
async function gate(request:Request,digest:string):Promise<Gate>{
  const cache=cacheDefault();
  if(!cache)return {allowed:false,pending:false};
  const now=Date.now(), ip=request.headers.get("cf-connecting-ip")??"unknown";
  const hour=Math.floor(now/3_600_000);
  if(gateHour!==hour){gateHour=hour;gateHourlyCommitted=0;}
  const recent=queryDispatch.get(digest);
  if(recent&&recent.until>now)return {allowed:false,pending:recent.pending};
  if(gateDigests.has(digest)||gateIps.has(ip)||
      gateHourlyCommitted+gateInFlight>=GLOBAL_QUERIES_PER_HOUR)
    return {allowed:false,pending:false};
  gateDigests.add(digest);
  gateIps.add(ip);
  gateInFlight++;
  try{
    const marker=cacheRequest("request/"+digest);
    const old=await cache.match(marker);
    if(old){
      const pending=(await old.text())==="pending";
      boundedPut(queryDispatch,digest,{until:now+QUERY_COOLDOWN_SECONDS*1000,pending},128);
      return {allowed:false,pending};
    }
    // Reject IP and global quota without poisoning this digest for another
    // client. Only an actual HTTP attempt gets a ten-minute SHA marker.
    if(!await quota(request,"query-embed"))
      return {allowed:false,pending:false};
    const bucket=cacheRequest("hour/"+hour);
    const value=await cache.match(bucket);
    const count=value?Number(await value.text()):0;
    if(!Number.isFinite(count)||count>=GLOBAL_QUERIES_PER_HOUR||
        gateHourlyCommitted>=GLOBAL_QUERIES_PER_HOUR)
      return {allowed:false,pending:false};
    // Increment isolate state synchronously before the first awaited write.
    gateHourlyCommitted++;
    await cache.put(bucket,new Response(String(Math.max(count,gateHourlyCommitted)),{
      headers:{"cache-control":"public, max-age=3600"},
    }));
    // Mark once a real attempt has been reserved; 422/network failure also
    // consumes one attempt for the full ten-minute cooldown.
    await markGate(digest,false);
    return {allowed:true,pending:false};
  }catch{
    return {allowed:false,pending:false};
  }finally{
    gateDigests.delete(digest);
    gateIps.delete(ip);
    gateInFlight--;
  }
}
async function markGate(digest:string,pending:boolean):Promise<void>{
  boundedPut(queryDispatch,digest,{until:Date.now()+QUERY_COOLDOWN_SECONDS*1000,pending},128);
  const cache=cacheDefault();
  if(cache)await cache.put(cacheRequest("request/"+digest),
    new Response(pending?"pending":"failed",{
      headers:{"cache-control":"public, max-age="+QUERY_COOLDOWN_SECONDS},
    }));
}

const encoderCacheKey=(digest:string)=>cacheRequest("vector/"+digest);
interface QueryShardEntry {
  path:string; ids_path:string; count:number; sha256:string; ids_sha256:string;
}
async function queryFromShards(env:SemanticEnv,digest:string):Promise<Float32Array|null>{
  const raw=await bytes(asset(env,"queries/manifest.json"),100000,10);
  if(!raw)return null;
  const manifest=JSON.parse(new TextDecoder().decode(raw)) as {
    version:number;dimension:number;seal_capacity:number;max_queries:number;
    shards:QueryShardEntry[];tail:QueryShardEntry|null;
  };
  if(manifest.version!==1||manifest.dimension!==DIM||
      manifest.seal_capacity!==64||manifest.max_queries!==512||
      !Array.isArray(manifest.shards)||manifest.shards.length>8)
    throw new SemanticFault("query_vector_invalid");
  for(const entry of [...manifest.shards,...(manifest.tail?[manifest.tail]:[])]){
    if(!entry||!Number.isInteger(entry.count)||entry.count<1||entry.count>64||
      !DIGEST.test(entry.sha256)||!DIGEST.test(entry.ids_sha256)||
      !/^(shards\/(?:shard-[0-9]{6}|tail)-[0-9a-f]{64}\.f16)$/.test(entry.path)||
      !/^(ids\/(?:shard-[0-9]{6}|tail)-[0-9a-f]{64}\.json)$/.test(entry.ids_path)||
      !entry.path.endsWith(entry.sha256+".f16")||
      !entry.ids_path.endsWith(entry.ids_sha256+".json"))
      throw new SemanticFault("query_vector_invalid");
    const idsRaw=await bytes(asset(env,"queries/"+entry.ids_path),64*70,3600);
    if(!idsRaw||await sha256(idsRaw)!==entry.ids_sha256)
      throw new SemanticFault("query_vector_invalid");
    const ids=JSON.parse(new TextDecoder().decode(idsRaw)) as unknown;
    if(!Array.isArray(ids)||ids.length!==entry.count||ids.some(x=>typeof x!=="string"||!DIGEST.test(x)))
      throw new SemanticFault("query_vector_invalid");
    const index=ids.indexOf(digest);
    if(index<0)continue;
    const bin=await bytes(asset(env,"queries/"+entry.path),64*WIDTH,3600);
    if(!bin||bin.byteLength!==entry.count*WIDTH||await sha256(bin)!==entry.sha256)
      throw new SemanticFault("query_vector_invalid");
    return decodeFloat16(bin,index*WIDTH);
  }
  return null;
}
export async function queryVector(env:SemanticEnv,query:string,request?:Request):Promise<Float32Array|null>{
  const normalized=normalizedQuery(query);
  if(!normalized||normalized.length>200)return null;
  const digest=await sha256(normalized);
  const sharded=await queryFromShards(env,digest);
  if(sharded)return sharded;
  // Transitional M22 r2 compatibility for existing per-query JSON vectors.
  const raw=await bytes(asset(env,"queries/"+digest+".json"),8192,10);
  if(raw){
    const value=JSON.parse(new TextDecoder().decode(raw)) as {
      version:number;dimension:number;dtype:string;query_sha256:string;vector:string;
    };
    if(value.version!==1||value.dimension!==DIM||value.dtype!=="float16-le"||
      value.query_sha256!==digest||typeof value.vector!=="string"||value.vector.length>1600)
      throw new SemanticFault("query_vector_invalid");
    return decodeFloat16(Uint8Array.from(atob(value.vector),c=>c.charCodeAt(0)));
  }
  if(!env.QUERY_ENCODER_URL)return null;
  const url=encoderUrl(env.QUERY_ENCODER_URL,env.QUERY_ENCODER_HOSTS);
  if(!url)throw new SemanticFault("unsafe_query_encoder_url");
  const existing=encoderMemory.get(digest);
  if(existing&&existing.until>Date.now())return existing.vector;
  const cache=cacheDefault();
  const saved=cache?await cache.match(encoderCacheKey(digest)):undefined;
  if(saved){
    const payload=await boundedBody(saved,16384);
    const vec=normalizeEmbedding(JSON.parse(new TextDecoder().decode(payload)));
    boundedPut(encoderMemory,digest,{until:Date.now()+600000,vector:vec},128);
    return vec;
  }
  const allowed=await gate(request??new Request("https://clipoteca.invalid/v1/search"),digest);
  if(!allowed.allowed)return null;
  try{
    const response=await fetch(url.href,{
      method:"POST",redirect:"error",headers:{"content-type":"application/json"},
      body:JSON.stringify({query:normalized}),signal:AbortSignal.timeout(5000),
    });
    if(!response.ok)throw new SemanticFault("query_encoder_unavailable");
    const content=await boundedBody(response,16384);
    const parsed=JSON.parse(new TextDecoder().decode(content)) as {vector?:unknown};
    const vector=normalizeEmbedding(parsed.vector);
    boundedPut(encoderMemory,digest,{until:Date.now()+600000,vector},128);
    if(cache)try{
      await cache.put(encoderCacheKey(digest),Response.json([...vector],{
        headers:{"cache-control":"public, max-age=600"},
      }));
    }catch{/* in-isolate vector already cached */}
    return vector;
  }catch{return null;}
}
export async function queueQuery(request:Request,env:SemanticEnv,query:string):Promise<boolean>{
  // Never run a full ingest for the sole purpose of embedding a query.
  if(env.CLIPOTECA_QUERY_DISPATCH_MODE!=="1")return false;
  const token=env.CLIPOTECA_DISPATCH_TOKEN;
  if(!token)return false;
  const normalized=normalizedQuery(query);
  if(!normalized||normalized.length>200)return false;
  const digest=await sha256(normalized);
  const attempt=await gate(request,digest);
  if(!attempt.allowed)return attempt.pending;
  try{
    const response=await fetch(
      "https://api.github.com/repos/"+repository(env)+"/actions/workflows/ingest.yml/dispatches",
      {method:"POST",redirect:"error",headers:{
        authorization:"Bearer "+token,
        accept:"application/vnd.github+json",
        "content-type":"application/json",
        "user-agent":"clipoteca-worker",
      },body:JSON.stringify({ref:"main",inputs:{
        query:normalized,mode:"query_embedding",request_id:digest,
      }})},
    );
    if(response.status!==204)return false; // failed 422 stays blocked for >=10m
    await markGate(digest,true);
    return true;
  }catch{return false;}
}
function matchesFilters(clip:Clip,params:URLSearchParams):boolean{
  return (!params.has("aspect_ratio")||clip.media.aspect_ratio===params.get("aspect_ratio")) &&
    (!params.has("max_duration")||clip.media.duration_seconds<=Number(params.get("max_duration"))) &&
    (!params.has("min_duration")||clip.media.duration_seconds>=Number(params.get("min_duration"))) &&
    (!params.has("min_width")||clip.media.width>=Number(params.get("min_width"))) &&
    (!params.has("fps")||clip.media.fps>=Number(params.get("fps"))) &&
    (!params.has("source")||clip.source.provider===params.get("source")) &&
    (!params.has("language")||clip.source.source_language===params.get("language")) &&
    (!params.has("has_audio")||clip.media.has_audio===(params.get("has_audio")==="true"));
}
export function prepareSearchIndex(clips:Clip[]):Map<string,Set<string>>{
  return new Map(clips.filter(clip=>clip.status==="published").map(clip=>[clip.id,new Set(searchTokens([
    ...Object.values(clip.title),...Object.values(clip.description),
    ...clip.tags.es,...clip.tags.en,clip.source.source_title??"",
    ...(clip.source.keywords??[]),
  ].join(" ")))]));
}
function keywordMatches(clip:Clip,tokens:string[],index:Map<string,Set<string>>):boolean{
  const document=index.get(clip.id);
  return !!document && tokens.every(token=>document.has(token));
}
export async function semanticSearch(
  request:Request,env:SemanticEnv,query:string,filters:URLSearchParams,
  allowQueryDispatch=true, catalogue?:Clip[],
  precomputedIndex?:Map<string,Set<string>>,
  skipQueryVectorFetch=false,
):Promise<SemanticSearchResult>{
  const clips=catalogue??await loadCatalog(env);
  const eligible=clips.filter(c=>c.status==="published"&&matchesFilters(c,filters));
  const index=precomputedIndex??prepareSearchIndex(clips);
  const tokens=searchTokens(query);
  const lexical=eligible.filter(c=>keywordMatches(c,tokens,index));
  const cursor=Number(filters.get("cursor")??0);
  const limit=Number(filters.get("limit")??20);
  const oldPage=()=>({
    clips:lexical.slice(cursor,cursor+limit),
    next_cursor:cursor+limit<lexical.length?String(cursor+limit):null,
  });
  if(env.SEMANTIC_SEARCH!=="1")
    return {...oldPage(),semantic:"unavailable",semantic_reason:"semantic_disabled"};
  if(skipQueryVectorFetch)
    return {...oldPage(),semantic:"unavailable",semantic_reason:"query_vector_budget_exceeded"};
  try{
    // The manifest lookup is cheap/cached. Never spend an Actions run
    // encoding a query when there are no indexed clip embeddings at all.
    const {manifest}=await fetchManifest(env);
    if (manifest && manifest.shards.length === 0 && !manifest.tail) {
      return {...oldPage(), semantic:"unavailable", semantic_reason:"empty_corpus"};
    }
    if (!manifest && (await loadLegacy(env,budget(env))).ids.length === 0) {
      return {...oldPage(), semantic:"unavailable", semantic_reason:"empty_corpus"};
    }
    const queryEmbedding=await queryVector(env,query,request);
    if(!queryEmbedding){
      if(env.QUERY_ENCODER_URL)return {...oldPage(),semantic:"unavailable",
        semantic_reason:"query_encoder_unavailable"};
      const queued=allowQueryDispatch?await queueQuery(request,env,query):false;
      return {...oldPage(),semantic:queued?"pending":"unavailable",
        ...(!queued?{semantic_reason:"query_vector_unavailable"}:{})};
    }
    const corpus=await loadPackedCorpus(env);
    if(!corpus.ids.length)return {...oldPage(),semantic:"unavailable",
      semantic_reason:corpus.reason??"empty_corpus"};
    const eligibleIds=new Set(eligible.map(c=>c.id));
    const scored:{id:string;cosine:number}[]=[];
    for(let index=0;index<corpus.ids.length;index++){
      const id=corpus.ids[index]!;
      if(!eligibleIds.has(id))continue;
      // Work with an already decoded, contiguous Float32Array; no per-request
      // checksum or f16 conversion and no need to store 512D per clip object.
      const similarity=cosine(queryEmbedding,corpus.vectors.subarray(index*DIM,(index+1)*DIM));
      scored.push({id,cosine:similarity});
    }
    const selectedSemantic=selectSemanticMatches(scored,semanticThreshold(env));
    if(!selectedSemantic.length)return {
      ...oldPage(),semantic:"unavailable",semantic_reason:"no_semantic_match",
    };
    const fused=rrfFuse(lexical.map(x=>x.id),selectedSemantic);
    const byId=new Map(eligible.map(c=>[c.id,c]));
    const selected=fused.slice(cursor,cursor+limit);
    return {
      clips:selected.map(row=>byId.get(row.id)!).filter(Boolean),
      next_cursor:cursor+limit<fused.length?String(cursor+limit):null,
      semantic:"ready",
      scores:Object.fromEntries(selected.map(row=>[row.id,row.combined_score])),
      ...(corpus.reason?{semantic_reason:corpus.reason}:{}),
    };
  }catch(error){
    return {...oldPage(),semantic:"unavailable",
      semantic_reason:error instanceof SemanticFault?error.code:"semantic_error"};
  }
}
