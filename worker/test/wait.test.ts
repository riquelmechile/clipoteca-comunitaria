import {afterEach, describe, expect, it, vi} from "vitest";
import {pollWait, waitLimit, waitMedia, handleWait} from "../src/wait";
import worker from "../src/index";

const id="a".repeat(32);
const req=new Request("https://clipoteca.test/v1/videos/"+id+"/esperar");
const sample=(listo:boolean)=>({done:listo,response:Response.json({listo})});

describe("bounded waits",()=>{
  afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals();});
  it("clamps all callers to 300 seconds and lower operator caps",()=>{
    expect(waitLimit(undefined)).toBe(300);
    expect(waitLimit(10000,"10000")).toBe(300);
    expect(waitLimit(300,"240")).toBe(240);
    expect(()=>waitLimit(0)).toThrow();
  });
  it("ends before cap when terminal",async()=>{
    vi.useFakeTimers();
    const get=vi.fn().mockResolvedValueOnce(sample(false)).mockResolvedValueOnce(sample(true));
    const pending=pollWait(get,300,{signal:new AbortController().signal});
    await vi.advanceTimersByTimeAsync(10_000);
    expect(await (await pending).json()).toEqual({listo:true});
    expect(get).toHaveBeenCalledTimes(2);
  });
  it("returns listo:false at timeout and emits heartbeat",async()=>{
    vi.useFakeTimers();
    const get=vi.fn(async()=>sample(false));
    const progress=vi.fn();
    const pending=pollWait(get,20,{signal:new AbortController().signal,progress});
    await vi.advanceTimersByTimeAsync(20_000);
    expect(await (await pending).json()).toEqual({listo:false});
    expect(progress).toHaveBeenCalledWith(15);
    expect(get).toHaveBeenCalledTimes(2);
  });
  it("cancels the pending poll when disconnected",async()=>{
    vi.useFakeTimers();
    const controller=new AbortController();
    const get=vi.fn(async()=>sample(false));
    const pending=pollWait(get,300,{signal:controller.signal});
    const rejected=expect(pending).rejects.toBeDefined();
    await vi.advanceTimersByTimeAsync(1000);
    controller.abort(new Error("disconnect"));
    await rejected;
    await vi.advanceTimersByTimeAsync(300_000);
    expect(get).toHaveBeenCalledTimes(1);
  });
  it("returns final media quality and rejection",async()=>{
    const fetcher=vi.fn(async(url:string|URL|Request)=>{
      const ref=String(url).endsWith("/119")?"u77":"u78";
      return Response.json({number:ref==="u77"?119:120,author_association:"OWNER",
        title:"[media-v1] "+"a".repeat(64),body:JSON.stringify({registry:"clipoteca-media-v1",
        origin:"url",kind:"video",status:ref==="u77"?"registered":"rejected",
        width:464,height:688,low_resolution:true,reason:"resolution_too_low"})});
    });
    vi.stubGlobal("fetch",fetcher);
    const result=await waitMedia(req,{}, {media_ids:["u77","u78"]},{signal:req.signal});
    expect(await result.json()).toMatchObject({listo:true,files:[
      {media_id:"u77",status:"registered",low_resolution:true,width:464,height:688},
      {media_id:"u78",status:"rejected",reason:"resolution_too_low"},
    ]});
  });
  it("provides a safe same-origin tracker",async()=>{
    const response=await worker.fetch(new Request("https://clipoteca.test/v1/videos/"+id+"/ver"));
    expect(response.status).toBe(200);
    expect(response.headers.get("content-security-policy")).toContain("connect-src 'self'");
    expect(await response.text()).toContain("Descargar video");
  });
  it("rejects ambiguous wait arguments",async()=>{
    const response=await handleWait(new Request("https://clipoteca.test/v1/media/esperar",{
      method:"POST",body:JSON.stringify({session_id:"a".repeat(64),media_ids:["u77"]}),
    }),{});
    expect(response?.status).toBe(400);
  });
});
