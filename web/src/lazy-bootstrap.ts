import "./app-wasm.js";

type FeatureLoader = () => Promise<unknown>;

/** Preserve the controllers' existing click initializers after their first import. */
export function installLazyFeatures(
  tabs: HTMLElement,
  loaders: Record<string, FeatureLoader>,
) {
  const loaded = new Set<string>();
  const pending = new Map<string, Promise<unknown>>();
  let activation = 0;
  const feedback = document.createElement("p");
  feedback.className = "build-note";
  feedback.setAttribute("role", "status");
  feedback.hidden = true;
  tabs.after(feedback);
  tabs.addEventListener(
    "click",
    (event) => {
      const tab = (event.target as Element | null)?.closest<HTMLButtonElement>(
        '[role="tab"]',
      );
      if (!tab || !tabs.contains(tab) || tab.disabled || tab.hidden) return;
      const selected = ++activation;
      const name = tab.dataset.tab || "";
      const load = loaders[name];
      feedback.hidden = true;
      if (!load || loaded.has(name)) return;
      // Stop this click before the tab and feature handlers run, then replay once.
      event.preventDefault();
      event.stopImmediatePropagation();
      feedback.textContent = `Loading ${tab.textContent?.trim() || "tool"}…`;
      feedback.hidden = false;
      tab.setAttribute("aria-busy", "true");
      let loading = pending.get(name);
      if (!loading) {
        loading = Promise.resolve().then(load);
        pending.set(name, loading);
      }
      void loading.then(
        () => {
          loaded.add(name);
          pending.delete(name);
          tab.removeAttribute("aria-busy");
          if (selected !== activation) return;
          feedback.hidden = true;
          tab.click();
        },
        () => {
          pending.delete(name);
          tab.removeAttribute("aria-busy");
          if (selected !== activation) return;
          feedback.textContent =
            "Could not load this tool. Select its tab to retry.";
          feedback.hidden = false;
        },
      );
    },
    { capture: true },
  );
}

const tabs = document.querySelector<HTMLElement>('[role="tablist"]');
if (tabs) {
  installLazyFeatures(tabs, {
    hair: () => import("./hair-wasm.js"),
    object: () => import("./object-wasm.js"),
    painting: () => import("./painting-wasm.js"),
    sim: () => import("./sim-wasm.js"),
  });
}
