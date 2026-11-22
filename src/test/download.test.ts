import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { createServer, ServerResponse } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, test } from "node:test";
import { downloadModel, ModelManifest, validateManifest, verifyModel } from "../download";

const cleanups: (() => Promise<void>)[] = [];
afterEach(async () => { for (const cleanup of cleanups.splice(0)) await cleanup(); });

async function fixture(body: Buffer, handler?: (response: ServerResponse) => void) {
  let calls = 0;
  const directory = await mkdtemp(join(tmpdir(), "btl-download-"));
  cleanups.push(() => rm(directory, { recursive: true, force: true }));
  const server = createServer((_request, response) => {
    calls++;
    if (handler) return handler(response);
    response.writeHead(200, { "content-length": body.length });
    response.end(body);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  cleanups.unshift(() => new Promise<void>(resolve => { server.closeAllConnections(); server.close(() => resolve()); }));
  const address = server.address() as { port: number };
  const manifest: ModelManifest = {
    name: "test baseline", filename: "model.gguf", url: `http://127.0.0.1:${address.port}/model.gguf`,
    sha256: createHash("sha256").update(body).digest("hex"), sizeBytes: body.length,
    revision: "a".repeat(40), tuned: false,
  };
  return { manifest, directory, destination: join(directory, "model.gguf"), calls: () => calls };
}

test("download verifies checksum and reuses a verified cache without network", async () => {
  const body = Buffer.from("GGUF fixture model");
  const f = await fixture(body);
  const progress: number[] = [];
  await downloadModel(f.manifest, f.destination, { allowHttpForTests: true, onProgress: bytes => progress.push(bytes) });
  assert.deepEqual(await readFile(f.destination), body);
  assert.equal(progress.at(-1), body.length);
  await downloadModel(f.manifest, f.destination, { allowHttpForTests: true });
  assert.equal(f.calls(), 1);
  assert.deepEqual(await readdir(f.directory), ["model.gguf"]);
});

test("same-size corrupt cache is detected and replaced only by verified bytes", async () => {
  const f = await fixture(Buffer.from("correct model"));
  await writeFile(f.destination, "corrupt model");
  assert.equal(await verifyModel(f.destination, f.manifest), false);
  await downloadModel(f.manifest, f.destination, { allowHttpForTests: true });
  assert.equal(await verifyModel(f.destination, f.manifest), true);
});

test("checksum mismatch removes partial file and preserves previous cache", async () => {
  const f = await fixture(Buffer.from("correct model"));
  await writeFile(f.destination, "previous data");
  await assert.rejects(downloadModel({ ...f.manifest, sha256: "f".repeat(64) }, f.destination, { allowHttpForTests: true }), /verification failed/);
  assert.equal(await readFile(f.destination, "utf8"), "previous data");
  assert.deepEqual(await readdir(f.directory), ["model.gguf"]);
});

test("cancellation aborts streaming and removes the partial download", async () => {
  const body = Buffer.alloc(8192, 7);
  const f = await fixture(body, response => {
    response.writeHead(200, { "content-length": body.length });
    response.write(body.subarray(0, 256));
    const timer = setTimeout(() => response.end(body.subarray(256)), 1000);
    response.once("close", () => clearTimeout(timer));
  });
  const controller = new AbortController();
  await assert.rejects(downloadModel(f.manifest, f.destination, {
    allowHttpForTests: true, signal: controller.signal,
    onProgress: received => { if (received > 0) controller.abort(); },
  }), /abort|cancel/i);
  assert.deepEqual(await readdir(f.directory), []);
});

test("HTTPS is mandatory except for explicitly enabled loopback fixtures", async () => {
  const f = await fixture(Buffer.from("model"));
  await assert.rejects(downloadModel(f.manifest, f.destination), /HTTPS/);
  assert.equal(f.calls(), 0);
  await assert.rejects(downloadModel({ ...f.manifest, url: "http://example.com/model" }, f.destination, { allowHttpForTests: true }), /HTTPS/);
});

test("size mismatch fails before a model is promoted", async () => {
  const f = await fixture(Buffer.from("model"), response => response.end("different size"));
  await assert.rejects(downloadModel(f.manifest, f.destination, { allowHttpForTests: true }), /size/);
  assert.deepEqual(await readdir(f.directory), []);
});

test("manifest rejects traversal, missing checksums and unpinned revisions", () => {
  const manifest: ModelManifest = {
    name: "baseline", filename: "model.gguf", url: "https://example.com/model.gguf", sha256: "a".repeat(64),
    sizeBytes: 123, revision: "b".repeat(40), tuned: false,
  };
  assert.deepEqual(validateManifest(manifest), manifest);
  for (const change of [{ filename: "../model.gguf" }, { sha256: "unknown" }, { revision: "main" }, { sizeBytes: -1 }, { tuned: undefined }]) {
    assert.throws(() => validateManifest({ ...manifest, ...change }), /invalid/);
  }
});
