/** Durable admission and session state; no bytes or credentials stored here. */
export const SESSION_ID=/^[a-f0-9]{64}$/;
export const SESSION_TTL=24*60*60*1000;
const MIB=1024*1024;
export type FileEntry={media_id:string;kind:"image"|"video"};
type Ip={hour:string;sessions:number;tickets:number;bytes:number};
export type BatchState={expires?:number;files?:FileEntry[];ips?:Record<string,Ip>;sessions?:number;tickets?:number;bytes?:number};
export type BatchCommand={action:"session_create"|"session_add"|"session_status"|"limit_session"|"limit_ticket"|"limit_bytes";ip?:string;file?:FileEntry;bytes?:number};
export function batchTransition(old:BatchState|undefined,c:BatchCommand,now:number):{code:number;state?:BatchState;write?:boolean}{
  if(c.action==="session_create"){
    if(old)return {code:409};
    return {code:201,state:{expires:now+SESSION_TTL,files:[]},write:true};
  }
  if(c.action==="session_status"||c.action==="session_add"){
    if(!old?.files||!old.expires)return {code:404};
    if(c.action==="session_status")return {code:200,state:old};
    if(old.expires<=now)return {code:410};
    if(old.files.length>=12)return {code:409};
    if(!c.file||!/^a\d{4}-\d{2}-[a-f0-9]{32}$/.test(c.file.media_id)||
      !["image","video"].includes(c.file.kind))return {code:400};
    return {code:200,state:{...old,files:[...old.files,c.file]},write:true};
  }
  if(!c.ip||!/^[a-f0-9]{64}$/.test(c.ip))return {code:400};
  const hour=new Date(now).toISOString().slice(0,13);
  const state=old??{ips:{},sessions:0,tickets:0,bytes:0};
  const previous=state.ips?.[c.ip];
  const ip:Ip={hour,sessions:previous?.hour===hour?previous.sessions:0,
    tickets:previous?.hour===hour?previous.tickets:0,bytes:previous?.bytes??0};
  let {sessions=0,tickets=0,bytes=0}=state;
  if(c.action==="limit_session"){
    if(ip.sessions>=4||sessions>=100)return {code:429};
    ip.sessions++;sessions++;
  }else if(c.action==="limit_ticket"){
    if(ip.tickets>=24||tickets>=200)return {code:429};
    ip.tickets++;tickets++;
  }else if(c.action==="limit_bytes"){
    const n=c.bytes??0;
    if(!Number.isSafeInteger(n)||n<=0||n>100*MIB)return {code:400};
    if(ip.bytes+n>200*MIB||bytes+n>2048*MIB)return {code:429};
    ip.bytes+=n;bytes+=n;
  }else return {code:400};
  return {code:200,state:{ips:{...state.ips,[c.ip]:ip},sessions,tickets,bytes},write:true};
}
