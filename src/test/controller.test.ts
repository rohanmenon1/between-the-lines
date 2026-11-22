import { strict as assert } from "node:assert";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { AnalysisQueue, isCurrent, sourceHash } from "../controller";

test("freshness checks reject edits, undo to the same text, and other files", () => {
  const original = { uri: "file:///a.py", version: 3, source: "def f():\n    pass\n" };
  const hash = sourceHash(original.source);
  assert.equal(isCurrent(original, { ...original }, hash), true);
  assert.equal(isCurrent(original, { ...original, version: 5 }, hash), false);
  assert.equal(isCurrent(original, { ...original, source: original.source + "# edit\n" }, hash), false);
  assert.equal(isCurrent(original, { ...original, uri: "file:///b.py" }, hash), false);
});

test("SHA-256 matches worker UTF-8 hashing for non-ASCII source", () => {
  assert.equal(sourceHash("é😀\n"), "f26b68b6994ea006d176890661e1c0401f133bb6cffe91cecfc5bd0570921cb8");
});

test("rapid saves replace queued analysis for a file", async () => {
  const executed: number[] = [];
  const queue = new AnalysisQueue<number>(async value => { executed.push(value); }, reason => { throw reason; });
  try {
    queue.enqueue("a", 1, 20);
    queue.enqueue("a", 2, 20);
    await delay(70);
    assert.deepEqual(executed, [2]);
  } finally { queue.dispose(); }
});

test("queue serializes files and invalidates running results", async () => {
  const executed: number[] = [];
  const latest: boolean[] = [];
  let release: (() => void) | undefined;
  let entered: (() => void) | undefined;
  const started = new Promise<void>(resolve => { entered = resolve; });
  const blocked = new Promise<void>(resolve => { release = resolve; });
  const queue = new AnalysisQueue<number>(async (value, isLatest) => {
    executed.push(value);
    if (value === 1) { entered!(); await blocked; }
    latest.push(isLatest());
  }, reason => { throw reason; });
  try {
    queue.enqueue("a", 1, 0);
    await started;
    queue.enqueue("b", 2, 0);
    queue.invalidate("a");
    await delay(15);
    assert.deepEqual(executed, [1]);
    release!();
    await delay(40);
    assert.deepEqual(executed, [1, 2]);
    assert.deepEqual(latest, [false, true]);
  } finally { release?.(); queue.dispose(); }
});

test("dispose cancels queued work and marks in-flight work stale", async () => {
  let check: (() => boolean) | undefined;
  let release: (() => void) | undefined;
  let entered: (() => void) | undefined;
  const started = new Promise<void>(resolve => { entered = resolve; });
  const blocked = new Promise<void>(resolve => { release = resolve; });
  const executed: number[] = [];
  const queue = new AnalysisQueue<number>(async (value, latest) => {
    executed.push(value); check = latest; entered!(); await blocked;
  }, reason => { throw reason; });
  queue.enqueue("a", 1, 0);
  await started;
  queue.enqueue("b", 2, 0);
  queue.dispose();
  assert.equal(check!(), false);
  release!();
  await delay(25);
  assert.deepEqual(executed, [1]);
});

test("an analysis failure does not prevent the next file from running", async () => {
  const errors: unknown[] = [];
  const completed: number[] = [];
  const queue = new AnalysisQueue<number>(async value => {
    if (value === 1) throw new Error("worker exited");
    completed.push(value);
  }, error => { errors.push(error); });
  try {
    queue.enqueue("a", 1, 0); queue.enqueue("b", 2, 0);
    await delay(50);
    assert.equal(errors.length, 1);
    assert.deepEqual(completed, [2]);
  } finally { queue.dispose(); }
});
