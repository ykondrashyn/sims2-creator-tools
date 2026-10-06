globalThis.document = {
  baseURI: "http://static.test/sims2-creator-tools/",
  querySelector: () => null,
};
import "fake-indexeddb/auto";
import test from "node:test";
import assert from "node:assert/strict";
import { source } from "./helpers.mjs";

for (const mode of ["image", "inference"])
  for (const stage of ["manifest", "assets", "long-running"]) {
    test(`Upscaling ${mode} releases its lease when ${stage} loading is interrupted`, async () => {
      const savedFetch = globalThis.fetch;
      const savedTimeout = globalThis.setTimeout;
      let started;
      const loading = new Promise((resolve) => {
        started = resolve;
      });
      const deadlines = [];
      const manifest = {
        schema_version: 1,
        protocol_version: 1,
        assets: Object.fromEntries(
          [
            "worker",
            "glue",
            "wasm",
            "local-upscale-worker",
            "local-upscale-runtime",
            "local-upscale-glue",
            "local-upscale-wasm",
            "local-upscale-model",
          ].map((name, i) => [
            name,
            { sha256: String(i + 1).repeat(64), size: 4, url: "/test/" + name },
          ]),
        ),
      };
      const { modelProfile } = await source("local-upscale/models.ts");
      const full = modelProfile("full");
      Object.assign(manifest.assets["local-upscale-model"], {
        sha256: full.sha256,
        size: full.size,
      });
      globalThis.fetch = (url) => {
        if (stage === "assets" && url.endsWith("manifest.json"))
          return Promise.resolve(new Response(JSON.stringify(manifest)));
        started();
        return new Promise(() => {});
      };
      if (stage === "long-running")
        globalThis.setTimeout = (fn, ms, ...args) => {
          if (ms === 600_000) {
            deadlines.push(fn);
            return 0;
          }
          return savedTimeout(fn, ms, ...args);
        };
      try {
        const controller = new AbortController();
        const images = await source(
          mode === "image" ? "upscale-image.ts" : "local-upscale/client.ts",
        );
        const operation =
          mode === "image"
            ? images.processImage(
                new Blob(["source stays local"]),
                {},
                controller.signal,
              )
            : images.upscaleLocally(
                new Blob(["source stays local"]),
                controller.signal,
                () => {},
              );
        await loading;
        if (stage === "long-running") {
          // Advancing past the former deadline must not abort local work.
          for (const expire of deadlines) expire();
          assert.equal(deadlines.length, 0);
          assert.equal(controller.signal.aborted, false);
        }
        controller.abort();
        await assert.rejects(operation, { name: "AbortError" });
        const store = await source("package-runtime/store.ts");
        const lease = await store.acquire("another-tool");
        await store.release(lease);
        assert.equal(await store.lease(), undefined);
        assert.deepEqual(await store.listJobs(), []);
      } finally {
        globalThis.fetch = savedFetch;
        globalThis.setTimeout = savedTimeout;
      }
    });
  }

const { MODEL_PROFILES } = await source("local-upscale/models.ts");
for (const profile of MODEL_PROFILES)
  test(`${profile.id} rejects a mismatched model before downloading inference assets`, async () => {
    const savedFetch = globalThis.fetch;
    const requests = [];
    globalThis.fetch = async (url) => {
      requests.push(url);
      assert.ok(url.endsWith("manifest.json"));
      return new Response(
        JSON.stringify({
          schema_version: 1,
          protocol_version: 1,
          assets: {
            [profile.asset]: { sha256: "0".repeat(64), size: profile.size },
          },
        }),
      );
    };
    try {
      const client = await source("local-upscale/client.ts");
      await assert.rejects(
        client.upscaleLocally(
          new Blob(["private input"]),
          new AbortController().signal,
          () => {},
          "wasm",
          profile,
        ),
        /does not match this release/,
      );
      assert.equal(requests.length, 1);
      const store = await source("package-runtime/store.ts");
      assert.equal(await store.lease(), undefined);
    } finally {
      globalThis.fetch = savedFetch;
    }
  });
