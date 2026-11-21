import assert from "node:assert/strict";
import { test } from "node:test";
import { WorkerClient, WorkerClientOptions, WorkerResponseError } from "../workerClient";

function worker(body: string, options: WorkerClientOptions = {}): WorkerClient {
  const program = `require('node:readline').createInterface({input:process.stdin}).on('line', line => { const q=JSON.parse(line); ${body} });`;
  return new WorkerClient(process.execPath, ["-e", program], { timeoutMs: 2000, ...options });
}

test("NDJSON responses can be fragmented across UTF-8 bytes", async () => {
  const client = worker("const b=Buffer.from(JSON.stringify({id:q.id,result:{text:'café'}})+'\\n'); process.stdout.write(b.subarray(0,38)); setTimeout(()=>process.stdout.write(b.subarray(38)),10);");
  try { assert.deepEqual(await client.request("status"), { text: "café" }); }
  finally { client.dispose(); }
});

test("structured worker errors reject one request without breaking the process", async () => {
  const client = worker("process.stdout.write(JSON.stringify(q.method==='bad'?{id:q.id,error:{code:'invalid',message:'Not allowed'}}:{id:q.id,result:true})+'\\n');");
  try {
    await assert.rejects(client.request("bad"), error => {
      assert.ok(error instanceof WorkerResponseError);
      assert.equal(error.code, "invalid");
      assert.equal(error.message, "Not allowed");
      return true;
    });
    assert.equal(await client.request("status"), true);
  } finally { client.dispose(); }
});

test("native stdout noise terminates the worker instead of accepting an ambiguous response", async () => {
  const client = worker("process.stdout.write('diagnostic text\\n');");
  await assert.rejects(client.request("analyze"), /invalid JSON/);
  assert.equal(client.isRunning, false);
});

test("unknown request ids terminate the worker", async () => {
  const client = worker("process.stdout.write(JSON.stringify({id:'other',result:{}})+'\\n');");
  await assert.rejects(client.request("analyze"), /invalid response/);
  assert.equal(client.isRunning, false);
});

test("timeouts terminate native work and reject pending requests", async () => {
  const client = worker("", { timeoutMs: 50 });
  await assert.rejects(client.request("analyze"), /timed out/);
  assert.equal(client.isRunning, false);
});

test("cancellation terminates the process to stop native CPU work", async () => {
  const client = worker("");
  const controller = new AbortController();
  const result = client.request("analyze", {}, controller.signal);
  controller.abort();
  await assert.rejects(result, /cancelled/);
  assert.equal(client.isRunning, false);
});

test("request queue and unterminated response size are bounded", async () => {
  const client = worker("process.stdout.write('x'.repeat(200));", { maxPending: 1, maxLineBytes: 100 });
  const first = assert.rejects(client.request("a"), /size limit/);
  await assert.rejects(client.request("b"), /queue is full/);
  await first;
});

test("worker exit rejects pending requests", async () => {
  const client = worker("process.exit(3);");
  await assert.rejects(client.request("analyze"), /exited/);
});

test("nonexistent executable fails cleanly", async () => {
  const client = new WorkerClient("btl-nonexistent-test-binary");
  await assert.rejects(client.request("status"), /Could not start|Could not write/);
  client.dispose();
});
