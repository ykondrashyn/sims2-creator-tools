import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const code = ts.transpileModule(
  readFileSync(new URL("../src/shared/autosave.ts", import.meta.url), "utf8"),
  {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const module = { exports: {} };
vm.runInThisContext(`(function(exports) {${code}\n})`)(module.exports);
const { createAutosaveQueue } = module.exports;
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

test("autosave retains one running and only the latest pending write", async () => {
  const queue = createAutosaveQueue();
  const gate = deferred(),
    started = deferred();
  const calls = [];
  const active = queue.enqueue(async () => {
    calls.push(1);
    started.resolve();
    await gate.promise;
    return 1;
  });
  await started.promise;
  const waiters = [];
  for (let revision = 2; revision <= 100; revision++) {
    waiters.push(
      queue.enqueue(async () => {
        calls.push(revision);
        return revision;
      }),
    );
  }
  assert.deepEqual(calls, [1]);
  assert.equal(new Set(waiters).size, 1);
  let flushed = false;
  const flush = queue.flush().then((value) => {
    flushed = true;
    return value;
  });
  await Promise.resolve();
  assert.equal(flushed, false);
  gate.resolve();
  assert.equal(await active, 1);
  assert.deepEqual(await Promise.all(waiters), Array(99).fill(100));
  assert.equal(await flush, 100);
  assert.deepEqual(calls, [1, 100]);
});

test("explicit build save waits for its pending write and exposes failures", async () => {
  const queue = createAutosaveQueue();
  const gate = deferred(),
    started = deferred();
  const first = queue.enqueue(async () => {
    started.resolve();
    await gate.promise;
  });
  await started.promise;
  let built = false;
  const stale = queue.enqueue(async () => {
    throw new Error("superseded should not execute");
  });
  const explicit = queue.enqueue(async () => {
    throw new Error("quota exceeded");
  });
  const building = explicit.then(() => {
    built = true;
  });
  const failure = assert.rejects(building, /quota exceeded/);
  const pendingFailure = assert.rejects(stale, /quota exceeded/);
  const flushFailure = assert.rejects(queue.flush(), /quota exceeded/);
  gate.resolve();
  await Promise.all([first, failure, pendingFailure, flushFailure]);
  assert.equal(built, false);
  assert.equal(await queue.enqueue(async () => "retry saved"), "retry saved");
});

test("a failed running save does not discard the latest queued state", async () => {
  const queue = createAutosaveQueue();
  const gate = deferred(),
    started = deferred();
  const first = queue.enqueue(async () => {
    started.resolve();
    await gate.promise;
    throw new Error("old failure");
  });
  await started.promise;
  const fail = assert.rejects(first, /old failure/);
  const latest = queue.enqueue(async () => "new state");
  gate.resolve();
  await fail;
  assert.equal(await latest, "new state");
  assert.equal(await queue.flush(), "new state");
});

test("flush includes saves enqueued by a completion callback", async () => {
  const queue = createAutosaveQueue();
  const gate = deferred();
  const first = queue.enqueue(async () => "first");
  first.then(() =>
    queue.enqueue(async () => {
      await gate.promise;
      return "last";
    }),
  );
  const flushing = queue.flush();
  await first;
  gate.resolve();
  assert.equal(await flushing, "last");
});

test("coalescing leaves protocol 1 pins, blobs and captured values intact", async () => {
  const queue = createAutosaveQueue();
  const blob = new Blob([new Uint8Array([0, 255, 7])]);
  const snapshot = Object.freeze({
    manifest: Object.freeze({
      protocol_version: 1,
      release: "historical-engine",
    }),
    parameters: Object.freeze({ height: 1.0000000000000002 }),
    snapshotHash: "pinned",
    blob,
  });
  const superseded = queue.enqueue(async () => {
    assert.fail("superseded save ran");
  });
  const saved = queue.enqueue(async () => snapshot);
  assert.equal(await superseded, snapshot);
  assert.equal(await saved, snapshot);
  assert.equal((await queue.flush()).blob, blob);
  assert.equal(await createAutosaveQueue().flush(), undefined);
});

function controllerFunction(controller, name) {
  const filename = new URL(`../src/${controller}-wasm.ts`, import.meta.url);
  const source = ts.createSourceFile(
    filename.pathname,
    readFileSync(filename, "utf8"),
    ts.ScriptTarget.Latest,
    true,
  );
  let found;
  function visit(node) {
    if (ts.isFunctionDeclaration(node) && node.name?.text === name)
      found = node.getText(source);
    ts.forEachChild(node, visit);
  }
  visit(source);
  assert.ok(found, `${controller} ${name} entrypoint exists`);
  return ts.transpileModule(found, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
}

for (const controller of ["painting", "sim"]) {
  test(`${controller} controller coalesces saves and rejects stale response publication`, async () => {
    const firstStarted = deferred(),
      lastStarted = deferred();
    const firstGate = deferred(),
      lastGate = deferred();
    const writes = [],
      messages = [];
    const manifest = {
      protocol_version: 1,
      release: "historical",
      sims: { items: [{ id: "am" }] },
    };
    const context = vm.createContext({
      createAutosaveQueue,
      structuredClone,
      Error,
      autosaves: createAutosaveQueue(),
      saving: Promise.resolve(),
      record: { id: "saved-batch", revision: 0, manifest, parameters: {} },
      revision: 0,
      selected: manifest.sims.items[0],
      manifest,
      manualFrame: false,
      priceEdited: false,
      input: "first",
      $: () => ({ value: "am" }),
      frozen: () => false,
      parameters: () => ({ title: context.input }),
      params: () => ({ title: context.input }),
      display: () => ({ comparison: "before" }),
      msg: (value) => messages.push(value),
      message: (value) => messages.push(value),
      errorMessage: (error) => error.message,
      runtime: {
        async saveDraft(candidate) {
          writes.push(candidate);
          if (writes.length === 1) {
            firstStarted.resolve();
            await firstGate.promise;
          } else {
            lastStarted.resolve();
            await lastGate.promise;
          }
          return { ...candidate, persisted: true };
        },
      },
    });
    vm.runInContext(controllerFunction(controller, "save"), context);
    const first = context.save();
    await firstStarted.promise;
    context.input = "obsolete";
    const obsolete = context.save();
    context.input = "latest";
    const latest = context.save();
    firstGate.resolve();
    await first;
    await lastStarted.promise;
    assert.equal(context.record.parameters.title, "latest");
    assert.equal(context.record.persisted, undefined);
    assert.deepEqual(messages, []);
    assert.deepEqual(
      writes.map((record) => record.parameters.title),
      ["first", "latest"],
    );
    lastGate.resolve();
    await Promise.all([obsolete, latest]);
    assert.equal(context.record.persisted, true);
    assert.equal(context.record.manifest, manifest);
    assert.deepEqual(messages, ["Saved in this browser."]);

    context.runtime.saveDraft = async () => {
      throw new Error("quota exhausted");
    };
    await assert.rejects(context.save(), /quota exhausted/);
    assert.equal(messages.at(-1), "Could not save: quota exhausted");
  });
}

test("hair source switch fences an in-flight autosave response", async () => {
  const started = deferred(),
    gate = deferred();
  const saved = { id: "old-source", revision: 2 };
  const context = vm.createContext({
    structuredClone,
    clearTimeout,
    autosaves: createAutosaveQueue(),
    saveTimer: undefined,
    sourceEpoch: 1,
    savedRecord: saved,
    selectedTemplate: { id: "template" },
    uploadedFiles: [],
    uploadSource: () => false,
    curveEditor: { colors: [] },
    required: (value) => value,
    bases: new Map(),
    capture: () => ({ hair_name: "captured" }),
    saveNote: {},
    controls() {},
    runtime: Promise.resolve({
      store: { id: () => "new-id" },
      async saveDraft(candidate) {
        started.resolve();
        await gate.promise;
        return candidate;
      },
    }),
  });
  vm.runInContext(controllerFunction("hair", "persist"), context);
  const promise = context.persist();
  await started.promise;
  context.sourceEpoch++;
  context.savedRecord = { id: "new-source", revision: 1 };
  gate.resolve();
  await promise;
  assert.equal(context.savedRecord.id, "new-source");
  assert.notEqual(context.saveNote.textContent, "Saved in this browser.");
});

test("object actions can retry after an autosave failure", async () => {
  let scheduled,
    retried = false;
  const messages = [];
  const context = vm.createContext({
    Error,
    clearTimeout,
    revision: 1,
    busy: false,
    timer: undefined,
    autosaves: createAutosaveQueue(),
    saving: Promise.resolve(),
    controls() {},
    setTimeout(fn) {
      scheduled = fn;
      return 1;
    },
    message(value) {
      messages.push(value);
    },
    async draft() {
      throw new Error("temporary save failure");
    },
  });
  vm.runInContext(
    controllerFunction("object", "scheduleSave") +
      controllerFunction("object", "action"),
    context,
  );
  context.scheduleSave();
  await scheduled();
  assert.equal(
    messages.at(-1),
    "Could not save this batch: temporary save failure",
  );
  await context.action(async () => {
    retried = true;
  });
  assert.equal(retried, true);
});
