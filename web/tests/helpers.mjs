import { build } from "esbuild";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
export async function source(name) {
  const directory = await mkdtemp(path.join(tmpdir(), "sims2-test-"));
  const file = path.join(directory, "module.mjs");
  await build({
    entryPoints: [new URL("../src/" + name, import.meta.url).pathname],
    bundle: true,
    platform: "node",
    format: "esm",
    outfile: file,
    logLevel: "silent",
  });
  return import(pathToFileURL(file));
}
