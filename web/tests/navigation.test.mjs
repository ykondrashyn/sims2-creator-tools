import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";

async function load(name) {
  const result = await build({
    entryPoints: [
      new URL(`../src/shared/${name}.ts`, import.meta.url).pathname,
    ],
    bundle: true,
    format: "esm",
    write: false,
  });
  return import(
    "data:text/javascript;base64," +
      Buffer.from(result.outputFiles[0].contents).toString("base64")
  );
}
const { destinations, resolveDestination } = await load("destinations");
const { createActivationLoader, createActivationSequence } =
  await load("navigation");

test("late successful or failed activation cannot replace the current destination", async () => {
  for (const fails of [false, true]) {
    let finish;
    const slow = new Promise((resolve, reject) => {
      finish = () => (fails ? reject(Error("Offline")) : resolve());
    });
    const activate = createActivationSequence({ sim: () => slow });
    const previous = activate("sim");
    assert.deepEqual(await activate("home"), { status: "ready" });
    finish();
    assert.equal(await previous, null);
  }
});

test("a repeated route joins its pending initialization but only its latest visit publishes", async () => {
  let finish,
    calls = 0;
  const slow = new Promise((resolve) => {
    finish = resolve;
  });
  const activate = createActivationSequence({
    painting: async () => {
      calls++;
      await slow;
    },
  });
  const first = activate("painting"),
    second = activate("painting");
  finish();
  assert.equal(await first, null);
  assert.deepEqual(await second, { status: "ready" });
  assert.equal(calls, 1);
});

test("Home is the default and all seven public routes retain internal IDs", () => {
  for (const hash of ["", "#", "#/", "#/home"])
    assert.equal(resolveDestination(hash).destination.id, "home");
  assert.deepEqual(
    destinations.map((d) => d.id),
    [
      "home",
      "package",
      "hair",
      "sim",
      "object",
      "painting",
      "texture",
      "upscale",
    ],
  );
  for (const destination of destinations) {
    assert.equal(
      resolveDestination(`#/${destination.route}`).destination.id,
      destination.id,
    );
    assert.equal(resolveDestination(`#/${destination.route}`).unknown, false);
  }
  for (const hash of [
    "#/missing",
    "#/../hair",
    "#workspace",
    "#/hair?unknown",
  ]) {
    assert.equal(resolveDestination(hash).unknown, true);
    assert.equal(resolveDestination(hash).destination.id, "home");
  }
});

test("concurrent activation and repeat visits initialize a tool once", async () => {
  let count = 0,
    finish;
  const wait = new Promise((resolve) => {
    finish = resolve;
  });
  const activate = createActivationLoader({
    hair: async () => {
      count++;
      await wait;
    },
  });
  const first = activate("hair");
  assert.equal(activate("hair"), first);
  await Promise.resolve();
  assert.equal(count, 1);
  finish();
  await first;
  await activate("hair");
  assert.equal(count, 1);
});

test("failed imports and delayed capability failures can retry without reinitializing successes", async () => {
  let attempts = 0,
    fail;
  const wait = new Promise((_, reject) => {
    fail = reject;
  });
  const activate = createActivationLoader({
    object: async () => {
      if (++attempts === 1) await wait;
    },
  });
  const first = activate("object");
  const failure = assert.rejects(first, /reference assets/);
  await activate("home");
  fail(Error("Missing reference assets"));
  await failure;
  await activate("object");
  await activate("object");
  assert.equal(attempts, 2);
});

test("unrelated tool activation and Home do not wait for a slow feature", async () => {
  let finish,
    loaded = false;
  const wait = new Promise((resolve) => {
    finish = resolve;
  });
  const activate = createActivationLoader({
    sim: () => wait,
    upscale: async () => {
      loaded = true;
    },
  });
  const pending = activate("sim");
  await activate("home");
  await activate("upscale");
  assert.equal(loaded, true);
  finish();
  await pending;
});
