import { state } from "./session.js";
let operations: Promise<unknown> = Promise.resolve();
export function exclusive<T>(fn: () => Promise<T>): Promise<T> {
  const next = operations.catch(() => {}).then(fn);
  operations = next.catch(() => {});
  return next;
}
export function idle() {
  if (state.active)
    throw new Error(
      "Finish or cancel the current package build before inspecting or previewing another batch.",
    );
}
