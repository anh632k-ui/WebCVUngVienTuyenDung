import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:http";
import path from "node:path";
import process from "node:process";

const root = process.cwd(), nextBin = path.join(root, "node_modules", "next", "dist", "bin", "next");
const npmCli = process.env.npm_execpath, backendPort = 3250, appPort = 3251;
const origin = `http://localhost:${appPort}`;
const jobId = "00000000-0000-4000-8000-000000000099";
const user = { id: "00000000-0000-4000-8000-000000000002", email: "hr@example.test", full_name: "HR Example", phone_number: null, role: "HR", is_active: true, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };
let job = { id: jobId, recruiter_id: user.id, revision: 2, title: "Backend Developer", job_level: "Junior", location: "Hà Nội", raw_content: "Python FastAPI", min_experience_years: 1, education_requirement: null, parsing_status: "PARSED", is_criteria_verified: true, w_skill: 0.5, w_semantic: 0.3, w_experience: 0.2, status: "DRAFT", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", parsed_at: "2026-01-01T00:00:01Z" };
let criteria = { job_id: jobId, revision: 2, min_experience_years: 1, education_requirement: null, is_criteria_verified: true, skills: [{ skill_id: 1, importance: "MANDATORY", min_years_required: 1 }] };
const received = [];
function json(res,status,body) { res.writeHead(status, { "Content-Type": "application/json" }); res.end(JSON.stringify(body)); }
async function getBody(req) { const chunks=[]; for await (const c of req) chunks.push(c); const text=Buffer.concat(chunks).toString("utf8"); return text ? JSON.parse(text) : null; }
const server = createServer(async(req,res)=>{
  const body=await getBody(req);
  const route=new URL(req.url,"http://localhost").pathname, auth=req.headers.authorization;
  received.push({route,method:req.method,auth,body,key:req.headers["idempotency-key"]});
  if(route==="/api/v1/users/me")return auth==="Bearer hr-token" ? json(res,200,{success:true,data:user}):auth==="Bearer candidate-token"?json(res,200,{success:true,data:{...user,role:"CANDIDATE"}}):json(res,401,{success:false,error:{code:"INVALID_ACCESS_TOKEN",message:"Invalid"}});
  if(auth!=="Bearer hr-token")return json(res,403,{success:false,error:{code:"INSUFFICIENT_PERMISSIONS",message:"Forbidden"}});
  if(route==="/api/v1/jobs"&&req.method==="GET")return json(res,200,{success:true,data:[job],meta:{page:1,limit:10,total_items:1,total_pages:1}});
  if(route==="/api/v1/jobs"&&req.method==="POST")return json(res,201,{success:true,data:job});
  if(route===`/api/v1/jobs/${jobId}`&&req.method==="GET")return json(res,200,{success:true,data:job});
  if(route===`/api/v1/jobs/${jobId}`&&req.method==="PUT"){job={...job,...body,revision:job.revision+1};return json(res,200,{success:true,data:job});}
  if(route===`/api/v1/jobs/${jobId}`&&req.method==="DELETE"){res.writeHead(204);return res.end();}
  if(route===`/api/v1/jobs/${jobId}/criteria`&&req.method==="GET")return json(res,200,{success:true,data:criteria});
  if(route===`/api/v1/jobs/${jobId}/criteria`&&req.method==="PUT"){criteria={...criteria,...body,is_criteria_verified:true,revision:criteria.revision+1};return json(res,200,{success:true,data:criteria});}
  if(route===`/api/v1/jobs/${jobId}/weights`&&req.method==="PUT"){job={...job,...body,revision:job.revision+1};return json(res,200,{success:true,data:job});}
  if(route===`/api/v1/jobs/${jobId}/status`&&req.method==="PATCH"){job={...job,status:body.status};return json(res,200,{success:true,data:job});}
  return json(res,404,{success:false,error:{code:"NOT_FOUND",message:"Not found"}});
});
function listen(){return new Promise((resolve,reject)=>{server.once("error",reject);server.listen(backendPort,"127.0.0.1",resolve);});}
function close(){return new Promise((resolve)=>server.close(resolve));}
function build(){assert.ok(npmCli);const p=spawnSync(process.execPath,[npmCli,"run","build"],{cwd:root,env:{...process.env,BACKEND_API_URL:`http://127.0.0.1:${backendPort}`,SITE_URL:"",SEO_INDEXING_ENABLED:"false"},stdio:"inherit"});assert.equal(p.status,0);}
const cookie="cvinsight_session=hr-token", headers={cookie,origin,"sec-fetch-site":"same-origin","content-type":"application/json"};
function call(route,init={}){return fetch(origin+route,{redirect:"manual",...init});}
async function verify(){
  assert.equal((await call("/api/hr/jobs")).status,401);
  assert.equal((await call("/api/hr/jobs",{headers:{cookie:"cvinsight_session=candidate-token"}})).status,403);
  const list=await call("/api/hr/jobs?page=1&limit=10",{headers:{cookie}});
  assert.equal(list.status,200);assert.equal((await list.json()).data[0].id,jobId);
  assert.match(list.headers.get("cache-control")??"",/no-store/);
  const page=await call("/hr/jobs",{headers:{cookie}});assert.equal(page.status,200);assert.match(await page.text(),/Quản lý JD|Mô tả công việc/);
  assert.equal((await call("/hr/jobs",{headers:{cookie:"cvinsight_session=candidate-token"},redirect:"manual"})).status,307);
  const detail=await call(`/api/hr/jobs/${jobId}`,{headers:{cookie}});assert.equal(detail.status,200);
  const criteria=await call(`/api/hr/jobs/${jobId}/criteria`,{headers:{cookie}});assert.equal(criteria.status,200);
  const createPayload={title:"Backend Developer",job_level:"Junior",raw_content:"Python FastAPI",w_skill:0.5,w_semantic:0.3,w_experience:0.2};
  const uuid="c44d4444-4444-4444-8444-444444444444";
  const create=await call("/api/hr/jobs",{method:"POST",headers:{...headers,"idempotency-key":uuid},body:JSON.stringify(createPayload)});
  assert.equal(create.status,201,await create.clone().text());
  assert.equal(received.find(x=>x.route==="/api/v1/jobs"&&x.method==="POST")?.key,uuid);
  const count=received.filter(x=>x.route==="/api/v1/jobs"&&x.method==="POST").length;
  const badCreate=await call("/api/hr/jobs",{method:"POST",headers:{...headers,"idempotency-key":uuid},body:JSON.stringify({...createPayload,role:"ADMIN"})});
  assert.equal(badCreate.status,422);assert.equal(received.filter(x=>x.route==="/api/v1/jobs"&&x.method==="POST").length,count);
  const evil=await call("/api/hr/jobs",{method:"POST",headers:{...headers,origin:"https://evil.example","idempotency-key":uuid},body:JSON.stringify(createPayload)});
  assert.equal(evil.status,403);
  const badWeight=await call(`/api/hr/jobs/${jobId}/weights`,{method:"PUT",headers,body:JSON.stringify({w_skill:0.5,w_semantic:0.3,w_experience:0.3})});assert.equal(badWeight.status,422);
  const updateWeight=await call(`/api/hr/jobs/${jobId}/weights`,{method:"PUT",headers,body:JSON.stringify({w_skill:0.5,w_semantic:0.3,w_experience:0.2,recalculate:true})});assert.equal(updateWeight.status,200);
  const payload=received.find(x=>x.route.endsWith("/weights"));assert.equal(payload.body.recalculate,true);
  const criteriaUpdate=await call(`/api/hr/jobs/${jobId}/criteria`,{method:"PUT",headers,body:JSON.stringify({min_experience_years:1,education_requirement:null,skills:[{skill_id:1,importance:"MANDATORY",min_years_required:1}]})});assert.equal(criteriaUpdate.status,200);
  const badCriteria=await call(`/api/hr/jobs/${jobId}/criteria`,{method:"PUT",headers,body:JSON.stringify({skills:[]})});assert.equal(badCriteria.status,422);
  const active=await call(`/api/hr/jobs/${jobId}/status`,{method:"PATCH",headers,body:JSON.stringify({status:"ACTIVE"})});assert.equal(active.status,200);
  const updated=await call(`/api/hr/jobs/${jobId}`,{method:"PUT",headers,body:JSON.stringify({title:"Backend Senior"})});assert.equal(updated.status,200);
  const removed=await call(`/api/hr/jobs/${jobId}`,{method:"DELETE",headers,body:"{}"});assert.equal(removed.status,204);
  const html=(await (await call(`/hr/jobs/${jobId}`,{headers:{cookie}})).text());assert.match(html,/noindex, nofollow/);assert.doesNotMatch(html,/hr-token/);
  const sitemap=await (await call("/sitemap.xml")).text();assert.doesNotMatch(sitemap,/\/hr\/jobs|\/api\/hr/i);
}
async function waitReady(){for(let i=0;i<100;i++){try{const r=await call("/");if(r.ok)return;}catch{/*starting*/}await new Promise(res=>setTimeout(res,250));}throw new Error("Server not ready");}
let app;
try{
  await listen();build();app=spawn(process.execPath,[nextBin,"start","-p",String(appPort)],{cwd:root,env:{...process.env,BACKEND_API_URL:`http://127.0.0.1:${backendPort}`,SITE_URL:"",SEO_INDEXING_ENABLED:"false"},stdio:"ignore"});
  await waitReady();await verify();console.log("Production HR JD smoke PASS");
}finally{if(app){app.kill();await Promise.race([new Promise(resolve=>app.once("exit",resolve)),new Promise(resolve=>setTimeout(resolve,3000))]);}await close();}
