import { manifest } from "./assets.js";
import { exclusive, idle } from "./queue.js";
import { boot, call, put, rpc } from "./session.js";
import type { RuntimeManifest } from "./types.js";
export function validateConversion(file: File, m: RuntimeManifest) {
  return exclusive(async () => {
    idle();
    if (!file || file.size > 32 * 1024 ** 2)
      throw new Error("Choose one PNG no larger than 32 MiB.");
    await boot(m || (await manifest()), "conversion");
    await put("validation-input", file);
    try {
      return await call("conversion_validate", { input: "validation-input" });
    } finally {
      await rpc("drop", { name: "validation-input" });
    }
  });
}
