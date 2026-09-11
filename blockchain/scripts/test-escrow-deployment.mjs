// 가스 가격 0 인 로컬 HTTP RPC 에 실제 CLI 배포 경로를 그대로 태워 본다.
// GCoin 배포 → 에스크로 preflight → Ignition 배포 → verify 까지 한 바퀴를 돌리고,
// 잘못된 Chain ID · 코드 없는 결제 토큰 · 재배포 · 잘못된 주소 검증까지 확인한다.
// 실제 SSAFY RPC 는 쓰지 않는다. 개인키는 하드햇 기본 계정 것만 쓴다.
import assert from "node:assert/strict";
import { network } from "hardhat";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { rm, readFile, writeFile } from "node:fs/promises";
import { resolve, sep } from "node:path";
import { mnemonicToAccount } from "viem/accounts";
const root = resolve(import.meta.dirname, "..");
assert.equal(process.cwd(), root, "Run this test from blockchain/");
const paths = ["ignition/deployments/chain-31337", "deployments/ssafy-31337"].map(p => resolve(root, p));
for (const p of paths) {
  assert.ok(p.startsWith(root + sep));
  assert.ok(!existsSync(p), "Smoke-test output already exists; refusing to overwrite " + p);
}
const ACCEPT_TIMEOUT = 600;
const EXECUTION_TIMEOUT = 86400;
const params = resolve(root, "ignition/parameters/ssafy-escrow.json");
const originalParams = await readFile(params);
const writeParams = (paymentToken) => writeFile(params, JSON.stringify({
  GPUJobEscrowModule: { paymentToken, acceptTimeout: ACCEPT_TIMEOUT, executionTimeout: EXECUTION_TIMEOUT },
}, null, 2) + "\n");
const server = await network.createServer({override:{allowBlocksWithSameTimestamp:true,initialBaseFeePerGas:0n,accounts:{accountsBalance:"0"},mining:{interval:200}}},"127.0.0.1",0);
const {port} = await server.listen();
const account = mnemonicToAccount("test test test test test test test test test test test junk");
const key = "0x" + Buffer.from(account.getHdKey().privateKey).toString("hex");
const env = {...process.env,SSAFY_RPC_URL:"http://127.0.0.1:"+port,SSAFY_CHAIN_ID:"31337",SSAFY_PRIVATE_KEY:key,GSC_INITIAL_HOLDER:account.address,GSC_CONTRACT_ADDRESS:"",ESCROW_CONTRACT_ADDRESS:""};
async function run(args, extra={}, success=true) {
  const result = await new Promise((resolveResult,reject)=>{
    const child=spawn(process.execPath,["node_modules/hardhat/dist/src/cli.js",...args],{cwd:root,env:{...env,...extra},windowsHide:true});
    let output="";
    child.stdout.on("data",x=>output+=x);
    child.stderr.on("data",x=>output+=x);
    child.on("error",reject);
    child.on("exit",exitCode=>resolveResult({exitCode,output}));
  });
  if (success) { console.log(result.output); assert.equal(result.exitCode,0); return result.output; }
  assert.notEqual(result.exitCode,0);
  console.log("Expected rejection: "+args.join(" "));
  return result.output;
}
async function rpc(method,params=[]) {
 const r=await fetch(env.SSAFY_RPC_URL,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({jsonrpc:"2.0",id:1,method,params})});
 const data=await r.json(); if(data.error) throw Error(data.error.message); return data.result;
}
const nonce = () => rpc("eth_getTransactionCount",[account.address,"latest"]);
try {
 // 결제 토큰을 먼저 로컬에 올린다. 에스크로는 이미 있는 GCoin 주소를 받아야 한다.
 await run(["ignition","deploy","ignition/modules/GCoin.ts","--network","ssafy"]);
 const deployed = JSON.parse(await readFile(resolve(paths[0],"deployed_addresses.json"),"utf8"));
 const token = deployed["GCoinModule#GCoin"];
 assert.ok(token, "Local GCoin address is required");
 assert.equal(await nonce(),"0x1");
 await writeParams(token);

 await run(["run","scripts/preflight-escrow.ts"],{SSAFY_CHAIN_ID:"31338"},false);
 await run(["run","scripts/preflight-escrow.ts"],{ESCROW_CONTRACT_ADDRESS:account.address},false);
 await writeParams(account.address);                       // 코드가 없는 EOA 주소
 await run(["run","scripts/preflight-escrow.ts"],{},false);
 await writeParams(token);

 const preflight = await run(["run","scripts/preflight-escrow.ts"]);
 for (const value of [token, String(ACCEPT_TIMEOUT), String(EXECUTION_TIMEOUT)]) {
   assert.ok(preflight.includes(value), "Preflight must print "+value);
 }
 assert.equal(await nonce(),"0x1","Preflight must not send a transaction");

 const deploy=["ignition","deploy","ignition/modules/GPUJobEscrow.ts","--network","ssafy","--parameters","ignition/parameters/ssafy-escrow.json"];
 await run(deploy);
 const afterDeploy = await nonce();
 assert.equal(afterDeploy,"0x2");
 await run(["run","scripts/verify-escrow.ts"],{SSAFY_PRIVATE_KEY:""});
 const exported = JSON.parse(await readFile(resolve(paths[1],"GPUJobEscrow.json"),"utf8"));
 assert.equal(exported.paymentToken.toLowerCase(), token.toLowerCase());
 assert.equal(exported.acceptTimeout, String(ACCEPT_TIMEOUT));
 assert.equal(exported.executionTimeout, String(EXECUTION_TIMEOUT));
 assert.equal(exported.nextJobId, "1");
 assert.equal(await rpc("eth_getBalance",[account.address,"latest"]),"0x0");

 await run(deploy);                                        // 기록이 있으면 다시 배포하지 않는다
 assert.equal(await nonce(),afterDeploy);
 await run(["run","scripts/verify-escrow.ts"],{ESCROW_CONTRACT_ADDRESS:account.address,SSAFY_PRIVATE_KEY:""},false);
 console.log("PASS: zero-native-balance deployment, three printed parameters, read-only verification, wrong chain/token/address rejection, idempotent redeploy.");
} finally {
 await server.close();
 for(const p of paths) {
   assert.ok(p.startsWith(root+sep));
   await rm(p,{recursive:true,force:true});
 }
 await writeFile(params,originalParams);
}
