import catalog from "../../../tools/local-upscale/models.json";
import type { LocalBackend } from "./backend.js";

export interface ModelProfile {
  readonly id: string;
  readonly name: string;
  readonly selection_id: string;
  readonly asset: string;
  readonly model: string;
  readonly sha256: string;
  readonly size: number;
  readonly scale: number;
  readonly tile: number;
  readonly overlap: number;
  readonly prepad: number;
  readonly precision: string;
  readonly normalization: string;
  readonly gpu_buffer_bytes: number;
  readonly description: string;
  readonly docs: string;
}
export interface LocalModel {
  readonly id: string;
  readonly name: string;
  readonly backend: LocalBackend;
  readonly profile: Readonly<ModelProfile>;
}
export const MODEL_PROFILES: readonly Readonly<ModelProfile>[] = Object.freeze(
  catalog.map((profile) => Object.freeze(profile)),
);
export function modelProfile(id: string): Readonly<ModelProfile> {
  const profile = MODEL_PROFILES.find((item) => item.id === id);
  if (!profile) throw new Error("Unknown local upscaling model.");
  return profile;
}
export const MODELS: readonly Readonly<LocalModel>[] = Object.freeze(
  MODEL_PROFILES.flatMap((profile) =>
    (["wasm", "webgpu"] as const).map((backend) =>
      Object.freeze({
        id: profile.selection_id + (backend === "webgpu" ? "-webgpu" : ""),
        name: profile.name + (backend === "webgpu" ? " WebGPU" : " CPU"),
        backend,
        profile,
      }),
    ),
  ),
);
