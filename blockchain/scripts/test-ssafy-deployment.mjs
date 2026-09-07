import assert from "node:assert/strict";
import { network } from "hardhat";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { rm, readFile, writeFile } from "node:fs/promises";
import { resolve, sep } from "node:path";
import { mnemonicToAccount } from "viem/accounts";
const root = resolve(import.meta.dirname, "..");
assert.equal(process.cwd(), root, "Run this test from blockchain/");
const paths = ["ignition/deployments/chain-31337", "deployments/ssafy-31337"].map(p => resolve(root,p));
for (const p of paths) {
  assert.ok(p.startsWith(root + sep));
  assert.ok(!existsSync(p), "Smoke-test output already exists; refusing to overwrite " + p);
}
const params = resolve(root,"ignition/parameters/ssafy.json");
const oldParams = existsSync(params) ? await readFile(params) : undefined;
const server = await network.createServer({override:{allowBlocksWithSameTimestamp:true,initialBaseFeePerGas:0n,accounts:{accountsBalance:"0"},mining:{interval:200}}},"127.0.0.1",0);
const {port} = await server.listen();
const account = mnemonicToAccount("test test test test test test test test test test test junk");
const key = "0x" + Buffer.from(account.getHdKey().privateKey).toString("hex");
const env = {...process.env,SSAFY_RPC_URL:"http://127.0.0.1:"+port,SSAFY_CHAIN_ID:"31337",SSAFY_PRIVATE_KEY:key,GSC_INITIAL_HOLDER:"0x70997970C51812dc3A010C7d01b50e0d17dc79C8",GSC_CONTRACT_ADDRESS:""};
async function run(args, extra={}, success=true) {
  const result = await new Promise((resolveResult,reject)=>{
    const child=spawn(process.execPath,["node_modules/hardhat/dist/src/cli.js",...args],{cwd:root,env:{...env,...extra},windowsHide:true});
    let output="";
    child.stdout.on("data",x=>output+=x);
    child.stderr.on("data",x=>output+=x);
    child.on("error",reject);
    child.on("exit",exitCode=>resolveResult({exitCode,output}));
  });
  if (success) { console.log(result.output); assert.equal(result.exitCode,0); }
  else { assert.notEqual(result.exitCode,0); console.log("Expected rejection: "+args.join(" ")); }
}
async function rpc(method,params=[]) {
 const r=await fetch(env.SSAFY_RPC_URL,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({jsonrpc:"2.0",id:1,method,params})});
 const data=await r.json(); if(data.error) throw Error(data.error.message); return data.result;
}
try {
 await run(["run","scripts/preflight-ssafy.ts"],{SSAFY_CHAIN_ID:"31338"},false);
 await run(["run","scripts/preflight-ssafy.ts"],{GSC_INITIAL_HOLDER:"0x0000000000000000000000000000000000000000"},false);
 await run(["run","scripts/preflight-ssafy.ts"]);
 assert.equal(await rpc("eth_getTransactionCount",[account.address,"latest"]),"0x0");
 const deploy=["ignition","deploy","ignition/modules/GCoin.ts","--network","ssafy","--parameters","ignition/parameters/ssafy.json"];
 await run(deploy);
 await run(["run","scripts/verify-ssafy.ts"],{SSAFY_PRIVATE_KEY:""});
 const nonce=await rpc("eth_getTransactionCount",[account.address,"latest"]);
 assert.equal(nonce,"0x1");
 const exported=JSON.parse(await readFile(resolve(paths[1],"GCoin.json"),"utf8"));
 assert.equal(exported.holderBalance,"1000000");
 console.log("Deployment nonce: 1; deployer native balance: "+await rpc("eth_getBalance",[account.address,"latest"]));
 assert.equal(await rpc("eth_getBalance",[account.address,"latest"]),"0x0");
 await run(deploy);
 assert.equal(await rpc("eth_getTransactionCount",[account.address,"latest"]),nonce);
 await run(["run","scripts/verify-ssafy.ts"],{GSC_CONTRACT_ADDRESS:account.address,SSAFY_PRIVATE_KEY:""},false);
 console.log("PASS: zero-native-balance deployment, read-only verification, wrong chain/holder/address rejection, idempotent redeploy.");
} finally {
 await server.close();
 for(const p of paths) {
   assert.ok(p.startsWith(root+sep));
   await rm(p,{recursive:true,force:true});
 }
 if(oldParams) await writeFile(params,oldParams); else await rm(params,{force:true});
}
