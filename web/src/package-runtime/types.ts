import type { RuntimeLimits } from "../generated/contracts.js";
/** V1 feature metadata is interpreted by the pinned Rust feature validators.
 * Keep unknown keys when restoring snapshots. Platform identities, files,
 * manifests and lifecycle fields have explicit types below.
 */
export type Metadata = Record<string, any>;
export type JobKind =
  | "conversion"
  | "tattoo"
  | "hair"
  | "object"
  | "painting"
  | "sim";
export type JobState =
  | "draft"
  | "building"
  | "complete"
  | "failed"
  | "cancelled"
  | "interrupted";
export interface Asset {
  sha256: string;
  size: number;
  mime: string;
  url: string;
  /** Optional wire representations. Integrity above always covers decoded bytes. */
  encodings?: { gzip?: { sha256: string; size: number } };
}
export interface RuntimeManifest {
  schema_version: 1;
  protocol_version: 1;
  release: string;
  assets: Record<string, Asset>;
  limits: RuntimeLimits;
  hair: Metadata;
  objects: Metadata;
  paintings: Metadata;
  sims: Metadata;
  conversion: Metadata;
  texture_encoders?: Metadata;
  package_compression?: Metadata;
}
export interface StoredFile {
  name: string;
  filename: string;
  blob: string;
  sha256: string;
  size: number;
}
export interface Checkpoint {
  name: string;
  blob: string;
  sha256: string;
  size?: number;
  color?: string;
  report?: Metadata;
}
export interface SavedJob {
  schema_version: number;
  id: string;
  kind: JobKind;
  revision: number;
  state: JobState;
  manifest: RuntimeManifest;
  parameters: Metadata;
  ui: Metadata;
  files: StoredFile[];
  template?: Metadata;
  label?: string;
  filename?: string;
  created?: number;
  updated: number;
  snapshot?: Metadata;
  snapshotHash?: string | null;
  checkpoints?: Metadata[];
  runtimeRefs?: string[];
  output?: Metadata;
  report?: Metadata;
  error?: Metadata;
  message?: string;
  progress?: number | Metadata;
  modelSource?: StoredFile | null;
  optimization?: Metadata | null;
  [metadata: string]: any;
}
export interface Context {
  id?: string;
  revision?: number;
}
export interface Attempt {
  id: string;
  token: string;
  cancelled?: boolean;
  timer?: number;
}
export interface WorkerMetrics {
  heap_bytes: number;
  milliseconds: number;
}
export interface PendingCall {
  resolve: (value: any) => void;
  reject: (reason: Error) => void;
  job: string;
  revision: number;
  attempt: string;
}
export interface FileInput {
  name: string;
  file: File;
}
export interface JobEventMap {
  job: CustomEvent<SavedJob>;
  persisted: CustomEvent<SavedJob>;
  deleted: CustomEvent<string>;
  refresh: Event;
}
export class JobEvents extends EventTarget {
  override addEventListener<K extends keyof JobEventMap>(
    type: K,
    listener: ((event: JobEventMap[K]) => void) | null,
    options?: AddEventListenerOptions | boolean,
  ): void;
  override addEventListener(
    type: string,
    listener: EventListenerOrEventListenerObject | null,
    options?: AddEventListenerOptions | boolean,
  ): void;
  override addEventListener(
    type: string,
    listener: EventListenerOrEventListenerObject | null,
    options?: AddEventListenerOptions | boolean,
  ) {
    super.addEventListener(type, listener, options);
  }
}
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
