import type { Controls } from "../dom-controls.js";
/** Required form controls are resolved once by the feature's existing DOM ids. */
export type Control = HTMLElement &
  Pick<
    HTMLInputElement,
    | "required"
    | "validity"
    | "value"
    | "valueAsNumber"
    | "checked"
    | "disabled"
    | "accept"
    | "name"
    | "type"
    | "min"
    | "max"
    | "step"
  > & {
    files: FileList;
    options: HTMLOptionsCollection;
    selectedOptions: HTMLCollectionOf<HTMLOptionElement>;
    open: boolean;
    href: string;
    download: string;
    src: string;
    width: number;
    height: number;
    getContext(context: "2d"): CanvasRenderingContext2D | null;
    getContext(context: "bitmaprenderer"): ImageBitmapRenderingContext | null;
    setCustomValidity(message: string): void;
    reportValidity(): boolean;
    checkValidity(): boolean;
    reset(): void;
    add(option: HTMLOptionElement): void;
  };
export function control<K extends keyof Controls>(id: K): Controls[K];
export function control(id: string): Control;
export function control(id: string): HTMLElement {
  const element = document.getElementById(id);
  if (!element)
    throw new Error(`The page is missing control ${id}. Reload the website.`);
  return element;
}

/** Do not continue a restored workflow when its required state is missing. */
export function required<T>(value: T | null | undefined, name: string): T {
  if (value === null || value === undefined)
    throw new Error(
      `Missing ${name}. Reopen the saved batch or select its input again. Saved work is kept.`,
    );
  return value;
}

// Literal suffixes retain the corresponding element type. Dynamic controls,
// created by feature adapters, are resolved through the same existence check.
export function prefixedControls<P extends string>(prefix: P) {
  return <K extends string>(
    id: K,
  ): `${P}${K}` extends keyof Controls ? Controls[`${P}${K}`] : Control =>
    control(prefix + id) as `${P}${K}` extends keyof Controls
      ? Controls[`${P}${K}`]
      : Control;
}
