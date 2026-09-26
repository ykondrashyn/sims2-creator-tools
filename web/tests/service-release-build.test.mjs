import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { build } from "esbuild";

const root = new URL("../..", import.meta.url).pathname;
const artifacts = path.join(
  root,
  "artifacts/performance-implementation/service-release",
);
await fs.mkdir(artifacts, { recursive: true });
const output = await fs.mkdtemp(path.join(artifacts, "build-test-"));
await fs.writeFile(path.join(output, "package.json"), '{"type":"module"}\n');
const first = path.join(output, "one"),
  second = path.join(output, "two");
for (const out of [first, second]) {
  execFileSync(process.execPath, ["web/build.mjs", "--out", out], {
    cwd: root,
    stdio: "pipe",
  });
}
async function files(root) {
  const result = [];
  for (const entry of await fs.readdir(root, { withFileTypes: true })) {
    const name = path.join(root, entry.name);
    result.push(...(entry.isDirectory() ? await files(name) : [name]));
  }
  return result.sort();
}

test("two scoped frontend builds have identical file graphs and bytes", async () => {
  const a = await files(first),
    b = await files(second);
  assert.deepEqual(
    a.map((f) => path.relative(first, f)),
    b.map((f) => path.relative(second, f)),
  );
  for (let i = 0; i < a.length; i++) {
    assert.deepEqual(
      await fs.readFile(a[i]),
      await fs.readFile(b[i]),
      path.relative(first, a[i]),
    );
  }
});

test("public module paths resolve through shared chunks with standalone workers", async () => {
  const sources = (await files(path.join(root, "web/src"))).filter(
    (name) => name.endsWith(".ts") && !name.endsWith(".d.ts"),
  );
  const entries = [];
  for (const source of sources) {
    const relative = path
      .relative(path.join(root, "web/src"), source)
      .replace(/\.ts$/, ".js");
    const entry = path.join(first, relative);
    await fs.access(entry);
    entries.push(entry);
  }
  const graph = await build({
    entryPoints: entries,
    bundle: true,
    splitting: true,
    format: "esm",
    write: false,
    outdir: path.join(output, "resolution-check"),
    metafile: true,
    logLevel: "silent",
  });
  assert.deepEqual(
    graph.warnings.map((warning) => warning.text),
    [],
  );
  const startup = new Set();
  function visit(name) {
    if (startup.has(name)) return;
    startup.add(name);
    for (const dependency of graph.metafile.inputs[name]?.imports || []) {
      if (!dependency.external && dependency.kind !== "dynamic-import")
        visit(dependency.path);
    }
  }
  visit(
    Object.keys(graph.metafile.inputs).find(
      (name) => path.resolve(name) === path.join(first, "lazy-bootstrap.js"),
    ),
  );
  assert(startup.size > 1);
  assert(
    ![...startup].some((name) =>
      /\/(hair|object|painting|sim)-wasm\.js$/.test(name),
    ),
  );
  assert(![...startup].some((name) => /vendor\/three-preview\.js$/.test(name)));
  const startupCode = (
    await Promise.all([...startup].map((name) => fs.readFile(name, "utf8")))
  ).join("\n");
  assert.equal(startupCode.split("/api/").length - 1, 0);
  assert(
    Object.keys(graph.metafile.inputs).some((name) =>
      /package-runtime\/chunk-/.test(name),
    ),
  );
  for (const name of ["worker", "hash-worker"]) {
    const worker = path.join(first, "package-runtime", name + ".mjs");
    const result = await build({
      entryPoints: [worker],
      bundle: true,
      format: "esm",
      write: false,
      metafile: true,
    });
    assert.equal(Object.keys(result.metafile.inputs).length, 1);
    assert.deepEqual(
      await fs.readFile(worker),
      await fs.readFile(worker.replace(/\.mjs$/, ".js")),
    );
  }
  for (const file of await files(first)) {
    if (!file.endsWith(".js") || file.includes("/vendor/")) continue;
    const code = await fs.readFile(file, "utf8");
    for (const match of code.matchAll(
      /new URL\("(\.[^"]+)",import\.meta\.url\)/g,
    )) {
      await fs.access(path.resolve(path.dirname(file), match[1]));
    }
  }
});

test("direct runtime facades retain exports and share live module instances", async () => {
  const module = (name) => import(pathToFileURL(path.join(first, name)));
  const [store, facade, hash, hashFacade] = await Promise.all([
    module("package-runtime/store.js"),
    module("package-runtime/store.mjs"),
    module("package-runtime/sha256.js"),
    module("package-runtime/sha256.mjs"),
  ]);
  assert.equal(store.storageEvents, facade.storageEvents);
  assert.equal(store.putBlob, facade.putBlob);
  assert.equal(hash.sha256, hashFacade.sha256);
  const bytes = Uint8Array.from({ length: 1049 }, (_, i) => i % 256);
  assert.equal(
    hash.sha256(bytes),
    createHash("sha256").update(bytes).digest("hex"),
  );
});

test("HTML has one bootstrap entry and feature imports remain dynamic", async () => {
  const html = await fs.readFile(path.join(first, "index-wasm.html"), "utf8");
  assert.deepEqual(
    [...html.matchAll(/<script[^>]+src="([^"]+)"/g)].map((m) => m[1]),
    ["/static/lazy-bootstrap.js"],
  );
  const code = await fs.readFile(path.join(first, "lazy-bootstrap.js"), "utf8");
  for (const feature of ["hair", "object", "painting", "sim"]) {
    assert(code.includes(`import("./${feature}-wasm.js")`));
  }
});

test("lazy bootstrap replays only the latest activation, joins imports and retries failures", async () => {
  const documentBefore = globalThis.document;
  globalThis.document = {
    querySelector: () => null,
    createElement: () => ({ setAttribute() {}, hidden: true }),
  };
  try {
    const result = await build({
      entryPoints: [path.join(root, "web/src/lazy-bootstrap.ts")],
      bundle: true,
      format: "esm",
      write: false,
      plugins: [
        {
          name: "isolate-controller-registration",
          setup(builder) {
            builder.onResolve({ filter: /-wasm\.js$/ }, (args) => ({
              path: args.path,
              namespace: "fixture",
            }));
            builder.onLoad({ filter: /.*/, namespace: "fixture" }, () => ({
              contents: "export {};",
            }));
          },
        },
      ],
    });
    const { installLazyFeatures } = await import(
      "data:text/javascript;base64," +
        Buffer.from(result.outputFiles[0].contents).toString("base64")
    );
    const captures = [];
    const tabs = {
      after(node) {
        this.feedback = node;
      },
      contains: () => true,
      addEventListener(_, handler) {
        captures.push(handler);
      },
    };
    const activated = [];
    function tab(name) {
      return {
        dataset: { tab: name },
        textContent: name,
        closest() {
          return this;
        },
        setAttribute() {},
        removeAttribute() {},
        click() {
          let stopped = false;
          const event = {
            target: this,
            preventDefault() {},
            stopImmediatePropagation() {
              stopped = true;
            },
          };
          for (const handler of captures) handler(event);
          if (!stopped) activated.push(name);
        },
      };
    }
    const flush = async () => {
      for (let i = 0; i < 6; i++) await Promise.resolve();
    };
    let imports = 0,
      finish;
    const loading = new Promise((resolve) => {
      finish = resolve;
    });
    let attempts = 0;
    installLazyFeatures(tabs, {
      hair: () => {
        imports++;
        return loading;
      },
      object: () =>
        ++attempts === 1 ? Promise.reject(Error("offline")) : Promise.resolve(),
    });
    const hair = tab("hair"),
      conversion = tab("texture"),
      object = tab("object");
    hair.click();
    hair.click();
    await flush();
    assert.equal(imports, 1);
    assert.deepEqual(activated, []);
    conversion.click();
    finish();
    await flush();
    assert.deepEqual(activated, ["texture"]);
    hair.click();
    assert.deepEqual(activated, ["texture", "hair"]);
    object.click();
    await flush();
    assert.match(tabs.feedback.textContent, /retry/);
    object.click();
    await flush();
    assert.equal(attempts, 2);
    assert.deepEqual(activated, ["texture", "hair", "object"]);
  } finally {
    globalThis.document = documentBefore;
  }
});
