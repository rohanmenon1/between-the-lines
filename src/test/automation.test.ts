import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { mkdir, mkdtemp, readFile, realpath, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { ObservedEdit, readObservedEdits, readObservedEditsDetailed } from "../automation";

const hash = (source: string): string => createHash("sha256").update(source).digest("hex");

async function fixture() {
  const directory = await realpath(await mkdtemp(join(tmpdir(), "between-the-lines-events-")));
  const workspace = join(directory, "workspace");
  const inbox = join(directory, "inbox");
  await mkdir(workspace); await mkdir(inbox);
  const beforeSource = "def rank(items):\n    return items\n";
  const afterSource = "def rank(items):\n    return sorted(items)\n";
  const file = join(workspace, "rank.py");
  await writeFile(file, afterSource);
  const event: ObservedEdit = {
    kind: "edit", id: randomUUID(), eventId: randomUUID(), source: "codex", workspaceRoot: workspace,
    sessionId: "session-one", turnId: "turn-one", prompt: "rank must preserve input order for equal scores.",
    timestamp: "2026-10-25T12:00:00.000Z",
    files: [{ path: file, beforeSource, afterSource, beforeHash: hash(beforeSource), afterHash: hash(afterSource) }],
  };
  async function store(value: unknown = event, id = event.id): Promise<void> {
    await writeFile(join(inbox, `${id}.json`), JSON.stringify(value));
  }
  return { directory, workspace, inbox, file, event, store,
    close: () => rm(directory, { recursive: true, force: true }) };
}

test("observed edits retain their exact prompt, turn and originating event", async () => {
  const f = await fixture();
  try {
    await f.store();
    const [event] = await readObservedEdits(f.inbox, [f.workspace]);
    assert.equal(event.eventId, f.event.eventId);
    assert.equal(event.sessionId, "session-one");
    assert.equal(event.turnId, "turn-one");
    assert.equal(event.prompt, f.event.prompt);
    assert.equal(event.files[0].beforeSource, f.event.files[0].beforeSource);
    assert.equal(await readFile(f.file, "utf8"), f.event.files[0].afterSource, "Reading an event must not modify code");
    assert.deepEqual(await readObservedEdits(f.inbox, [f.workspace], new Set([event.id])), []);
  } finally { await f.close(); }
});

test("two sessions in one workspace keep separate prompt-to-edit associations", async () => {
  const f = await fixture();
  try {
    await f.store();
    const otherFile = join(f.workspace, "label.java");
    const source = "class Label {}\n";
    await writeFile(otherFile, source);
    const second: ObservedEdit = { ...f.event, id: randomUUID(), eventId: randomUUID(),
      sessionId: "session-two", turnId: "turn-two", prompt: "Label must retain its public name.",
      timestamp: "2026-10-25T12:00:01.000Z",
      files: [{ path: otherFile, beforeSource: "", afterSource: source, beforeHash: hash(""), afterHash: hash(source) }],
    };
    await f.store(second, second.id);
    const events = await readObservedEdits(f.inbox, [f.workspace]);
    assert.equal(events.length, 2);
    assert.deepEqual(events.map(event => [event.sessionId, event.prompt]), [
      [f.event.sessionId, f.event.prompt], [second.sessionId, second.prompt],
    ]);
  } finally { await f.close(); }
});

test("a captured prompt without completed edit evidence never authorizes annotation", async () => {
  const f = await fixture();
  try {
    await f.store({ ...f.event, kind: "prompt", files: undefined });
    const result = await readObservedEditsDetailed(f.inbox, [f.workspace]);
    assert.deepEqual(result.edits, []);
    assert.equal(result.skipped[0].id, f.event.id);
    assert.match(result.skipped[0].reason, /no completed edit association/);
  } finally { await f.close(); }
});

test("missing session or turn and fabricated before/after hashes are rejected", async () => {
  const f = await fixture();
  try {
    for (const replacement of [
      { sessionId: "" }, { turnId: undefined }, { eventId: "unproven" },
      { files: [{ ...f.event.files[0], beforeHash: "f".repeat(64) }] },
      { files: [{ ...f.event.files[0], afterHash: "f".repeat(64) }] },
    ]) {
      await f.store({ ...f.event, ...replacement });
      const result = await readObservedEditsDetailed(f.inbox, [f.workspace]);
      assert.equal(result.edits.length, 0);
      assert.equal(result.skipped.length, 1);
    }
  } finally { await f.close(); }
});

test("stale disk bytes are rejected and can be acknowledged to prevent replay after undo", async () => {
  const f = await fixture();
  try {
    await f.store();
    await writeFile(f.file, "def rank(items):\n    return []\n");
    const result = await readObservedEditsDetailed(f.inbox, [f.workspace]);
    assert.deepEqual(result.edits, []);
    assert.equal(result.skipped[0].id, f.event.id);
    await writeFile(f.file, f.event.files[0].afterSource);
    assert.deepEqual(await readObservedEdits(f.inbox, [f.workspace], new Set(result.skipped.map(item => item.id))), []);
  } finally { await f.close(); }
});

test("BOM-bearing completed edits use decoded source and exact raw freshness hashes", async () => {
  const f = await fixture();
  try {
    const original = f.event.files[0];
    await writeFile(f.file, `\uFEFF${original.afterSource}`);
    const file = { ...original, beforeRawHash: hash(`\uFEFF${original.beforeSource}`),
      afterRawHash: hash(`\uFEFF${original.afterSource}`) };
    await f.store({ ...f.event, files: [file] });
    const result = await readObservedEditsDetailed(f.inbox, [f.workspace]);
    assert.equal(result.edits.length, 1);
    assert.equal(result.edits[0].files[0].afterSource, original.afterSource);
    assert.equal(result.edits[0].files[0].afterHash, original.afterHash);
    assert.equal(result.edits[0].files[0].afterRawHash, file.afterRawHash);
    await writeFile(f.file, original.afterSource);
    assert.equal((await readObservedEdits(f.inbox, [f.workspace])).length, 0, "Removing the captured BOM invalidates freshness");
  } finally { await f.close(); }
});

test("raw hashes cannot fabricate bytes unrelated to decoded source", async () => {
  const f = await fixture();
  try {
    for (const field of ["beforeRawHash", "afterRawHash"]) {
      await f.store({ ...f.event, files: [{ ...f.event.files[0], [field]: "f".repeat(64) }] });
      const result = await readObservedEditsDetailed(f.inbox, [f.workspace]);
      assert.equal(result.edits.length, 0);
      assert.match(result.skipped[0].reason, /evidence is invalid/);
    }
    await writeFile(f.file, `\uFEFF${f.event.files[0].afterSource}`);
    await f.store();
    assert.equal((await readObservedEdits(f.inbox, [f.workspace])).length, 0, "Legacy hashes do not authorize BOM-bearing bytes");
  } finally { await f.close(); }
});

test("an event for a different workspace cannot annotate the open project", async () => {
  const f = await fixture();
  try {
    const other = join(f.directory, "other"); await mkdir(other);
    await f.store();
    const result = await readObservedEditsDetailed(f.inbox, [other]);
    assert.equal(result.edits.length, 0);
    assert.match(result.skipped[0].reason, /another workspace/);
  } finally { await f.close(); }
});

test("outside-workspace and duplicate file paths are rejected", async () => {
  const f = await fixture();
  try {
    const outside = join(f.directory, "private.py");
    await writeFile(outside, f.event.files[0].afterSource);
    await f.store({ ...f.event, files: [{ ...f.event.files[0], path: outside }] });
    assert.equal((await readObservedEditsDetailed(f.inbox, [f.workspace])).edits.length, 0);
    await f.store({ ...f.event, files: [f.event.files[0], f.event.files[0]] });
    const repeated = await readObservedEditsDetailed(f.inbox, [f.workspace]);
    assert.equal(repeated.edits.length, 0);
    assert.match(repeated.skipped[0].reason, /same changed file/);
  } finally { await f.close(); }
});

test("symlinked source files are never accepted as observed edit targets", async context => {
  const f = await fixture();
  try {
    const link = join(f.workspace, "linked.py");
    try { await symlink(f.file, link); }
    catch (error) {
      if (["EPERM", "EACCES"].includes((error as NodeJS.ErrnoException).code ?? "")) {
        context.skip("This Windows account cannot create symbolic links."); return;
      }
      throw error;
    }
    await f.store({ ...f.event, files: [{ ...f.event.files[0], path: link }] });
    assert.equal((await readObservedEditsDetailed(f.inbox, [f.workspace])).edits.length, 0);
  } finally { await f.close(); }
});

test("malformed, oversized and partial capture files do not enter the annotation queue", async () => {
  const f = await fixture();
  try {
    await writeFile(join(f.inbox, `${f.event.id}.json`), "{not json");
    assert.equal((await readObservedEditsDetailed(f.inbox, [f.workspace])).edits.length, 0);
    await writeFile(join(f.inbox, `${f.event.id}.json`), " ".repeat(2 * 1024 * 1024 + 1));
    const large = await readObservedEditsDetailed(f.inbox, [f.workspace]);
    assert.equal(large.edits.length, 0);
    assert.match(large.skipped[0].reason, /Invalid capture file/);
    await writeFile(join(f.inbox, `${randomUUID()}.part`), JSON.stringify(f.event));
    assert.equal((await readObservedEditsDetailed(f.inbox, [f.workspace])).edits.length, 0);
  } finally { await f.close(); }
});

test("unchanged records and ill-formed Unicode are not treated as code edits", async () => {
  const f = await fixture();
  try {
    await f.store({ ...f.event, files: [{ ...f.event.files[0], beforeSource: f.event.files[0].afterSource,
      beforeHash: f.event.files[0].afterHash }] });
    assert.equal((await readObservedEditsDetailed(f.inbox, [f.workspace])).edits.length, 0);
    const invalid = "def f():\n    return '\ud800'\n";
    await f.store({ ...f.event, files: [{ ...f.event.files[0], afterSource: invalid, afterHash: hash(invalid) }] });
    assert.equal((await readObservedEditsDetailed(f.inbox, [f.workspace])).edits.length, 0);
  } finally { await f.close(); }
});


test("only same-workspace completed-turn notices can report an unsupported edit", async () => {
  const f = await fixture();
  try {
    const notice = { kind: "stop", id: f.event.id, status: "noVerifiedEdit", verifiedFiles: 0,
      workspaceRoot: f.workspace, timestamp: f.event.timestamp };
    await f.store(notice);
    const result = await readObservedEditsDetailed(f.inbox, [f.workspace]);
    assert.equal(result.edits.length, 0);
    assert.equal(result.skipped[0].status, "noVerifiedEdit");
    await f.store({ ...notice, workspaceRoot: f.inbox });
    const elsewhere = await readObservedEditsDetailed(f.inbox, [f.workspace]);
    assert.equal(elsewhere.skipped[0].status, undefined);
  } finally { await f.close(); }
});
