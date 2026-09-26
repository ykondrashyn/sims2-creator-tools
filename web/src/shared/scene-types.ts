import type { Metadata } from "../package-runtime/types.js";
import type * as THREE from "../vendor/three-preview.js";
export type Vec3 = [number, number, number];
export interface ReferenceAssets {
  definition: Metadata;
  am: ArrayBuffer;
  af: ArrayBuffer;
  table?: ArrayBuffer;
}
export interface Layout {
  height: number;
  fit_to_template?: boolean;
  height_from_fit?: boolean;
  signature: string;
  matrix: number[];
  placement: { surface: string; tiles: [number, number][]; tile_count: number };
  requires_acknowledgement: boolean;
  warning: string | null;
  game_min: Vec3;
  game_max: Vec3;
  dimensions: { width: number; height: number; depth: number };
}
export interface AppearanceMaterial {
  image?: string;
  diffuse?: Vec3;
  double_sided?: boolean;
  blend?: string;
  alpha_test?: boolean;
}
export interface Appearance {
  materials: Record<string, AppearanceMaterial>;
  meshes: { glb: string }[];
}
export interface ObjectPreviewResult {
  original: Appearance;
  converted: Appearance;
  layout: Layout;
}
export function disposeMaterials(
  material: THREE.Material | THREE.Material[],
  textures = false,
) {
  for (const m of Array.isArray(material) ? material : [material]) {
    if (textures && "map" in m)
      (m as THREE.MeshStandardMaterial).map?.dispose();
    m.dispose();
  }
}
