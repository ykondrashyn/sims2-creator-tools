import { destinations } from "./shared/destinations.js";
import { installNavigation, type Activators } from "./shared/navigation.js";

const features: Activators = {
  texture: () =>
    import("./app-wasm.js").then((m) => m.activateAppTool("texture")),
  package: () =>
    import("./app-wasm.js").then((m) => m.activateAppTool("package")),
  hair: () => import("./hair-wasm.js").then((m) => m.activateHair()),
  object: () => import("./object-wasm.js").then((m) => m.activateObject()),
  painting: () =>
    import("./painting-wasm.js").then((m) => m.activatePainting()),
  sim: () => import("./sim-wasm.js").then((m) => m.activateSim()),
  upscale: () => import("./upscale.js").then((m) => m.attachUpscale()),
};
for (const destination of destinations) {
  const capability = destination.capability;
  const load = features[destination.id];
  if (!capability || !load) continue;
  features[destination.id] = async () => {
    const { manifest } = await import("./package-runtime/assets.js");
    const value = await manifest();
    if (!value[capability]?.items?.length)
      throw new Error(
        `${destination.label} is unavailable in this release because its reference assets are missing.`,
      );
    await load();
  };
}
installNavigation(features);
