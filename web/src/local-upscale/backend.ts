export type LocalBackend = "wasm" | "webgpu";

// Only the WebGPU surface used for availability checks. ORT owns the device.
export interface UpscaleAdapter {
  isFallbackAdapter?: boolean;
  info?: { isFallbackAdapter?: boolean };
  limits: { maxStorageBufferBindingSize: number; maxBufferSize: number };
}
interface GpuHost {
  isSecureContext: boolean;
  navigator: {
    gpu?: {
      requestAdapter(options: {
        powerPreference: "high-performance";
      }): Promise<UpscaleAdapter | null>;
    };
  };
}
export const GPU_UNAVAILABLE =
  "WebGPU is unavailable in this browser. Use a supported browser or choose a CPU upscaler.";

export async function gpuAdapter(
  host: GpuHost = globalThis as unknown as GpuHost,
  requiredBufferBytes = 256 * 1024 ** 2,
): Promise<UpscaleAdapter> {
  if (!host.isSecureContext)
    throw new Error(
      "WebGPU requires localhost on the computer hosting this site, or trusted HTTPS. Ordinary LAN HTTP cannot use WebGPU. Choose a CPU upscaler.",
    );
  if (!host.navigator.gpu) throw new Error(GPU_UNAVAILABLE);
  const adapter = await host.navigator.gpu.requestAdapter({
    powerPreference: "high-performance",
  });
  if (!adapter || adapter.isFallbackAdapter || adapter.info?.isFallbackAdapter)
    throw new Error(
      "No hardware WebGPU adapter is available. Enable browser graphics acceleration or choose a CPU upscaler.",
    );
  // Requirements are pinned per exported graph and its tile policy.
  if (
    adapter.limits.maxStorageBufferBindingSize < requiredBufferBytes ||
    adapter.limits.maxBufferSize < requiredBufferBytes
  )
    throw new Error(
      "This GPU cannot hold the model's tile buffers. Choose a CPU upscaler.",
    );
  return adapter;
}

export function backendAssets(backend: LocalBackend) {
  if (backend !== "wasm" && backend !== "webgpu")
    throw new Error("Unknown local upscaling backend.");
  const prefix =
    backend === "webgpu" ? "local-upscale-webgpu" : "local-upscale";
  return [prefix + "-runtime", prefix + "-glue", prefix + "-wasm"];
}
