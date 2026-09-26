import { control, type Control } from "./shared/dom.js";
type Channel = "value" | "red" | "green" | "blue";
type Point = [number, number];
type Definition =
  | { source: "editor"; points: Record<Channel, Point[]> }
  | { source: "gimp"; channels: Record<Channel, number[]>; filename: string };
export interface CustomColor {
  id: string;
  name: string;
  bin: number;
  kind: string;
  family: null;
  swatch: string;
  curve: Definition;
}
type CurveApi = (
  path: string,
  options?: { method: string; body: FormData },
) => Promise<{ json: () => Promise<{ curve: Definition }> }>;
type Action = (
  label: string,
  callback: () => Promise<void>,
  source?: boolean,
) => Promise<void>;

// Custom definitions live only in this page's current draft and its server job.
export class HairCurveEditor {
  declare api: CurveApi;
  declare action: Action;
  declare changed: (added: string | null, removed: string | null) => void;
  declare colors: CustomColor[];
  builtinNames: string[] = [];
  declare locked: boolean;
  declare $: (id: string) => Control;
  declare channels: Channel[];
  declare point: number;
  declare definition: Definition | null;
  declare drag: boolean;
  declare editId: string | null;

  constructor(
    api: CurveApi,
    action: Action,
    changed: (added: string | null, removed: string | null) => void,
  ) {
    this.api = api;
    this.action = action;
    this.changed = changed;
    this.colors = [];
    this.locked = false;
    this.$ = (id) => control("hair-curve-" + id);
    this.channels = ["value", "red", "green", "blue"];
    this.reset();
    this.$("mode").addEventListener("change", () => {
      this.resetDefinition();
      this.draw();
    });
    this.$("channel").addEventListener("change", () => {
      this.point = 0;
      this.draw();
    });
    this.$("point").addEventListener("change", () => {
      this.point = Number(this.$("point").value);
      this.draw();
    });
    this.$("input").addEventListener("change", () =>
      this.move(this.$("input").valueAsNumber, this.$("output").valueAsNumber),
    );
    this.$("output").addEventListener("change", () =>
      this.move(this.$("input").valueAsNumber, this.$("output").valueAsNumber),
    );
    this.$("reset").addEventListener("click", () => {
      if (this.definition?.source !== "editor") return;
      this.definition.points[this.channel()] = [
        [0, 0],
        [255, 255],
      ];
      this.point = 0;
      this.draw();
    });
    this.$("delete-point").addEventListener("click", () => this.deletePoint());
    this.$("add-point").addEventListener("click", () => {
      const points = this.points();
      if (points.length >= 32) return;
      let index = 0;
      for (let i = 1; i < points.length - 1; i++)
        if (
          points[i + 1][0] - points[i][0] >
          points[index + 1][0] - points[index][0]
        )
          index = i;
      const [a, b] = points.slice(index, index + 2);
      points.splice(index + 1, 0, [
        Math.round((a[0] + b[0]) / 2),
        Math.round((a[1] + b[1]) / 2),
      ]);
      this.point = index + 1;
      this.draw();
    });
    this.$("file").addEventListener("change", () => {
      const file = this.$("file").files[0];
      // Never retain a previous file's curve after an unsuccessful replacement.
      this.definition = null;
      this.draw();
      if (!file) return;
      void this.action("Reading curve…", async () => {
        try {
          if (file.size > 256 * 1024)
            throw new Error("Curve file exceeds 256 KiB.");
          const data = new FormData();
          data.append("file", file);
          const result = await (
            await this.api("/curves/parse", { method: "POST", body: data })
          ).json();
          this.definition = result.curve;
          this.error("");
          this.draw();
        } catch (errorCause) {
          const error =
            errorCause instanceof Error
              ? errorCause
              : new Error(String(errorCause));
          this.error(error.message);
        }
      });
    });
    this.$("save").addEventListener("click", () => this.save());
    this.$("cancel").addEventListener("click", () => {
      this.reset();
      this.$("section").open = false;
    });
    const graph = this.$("graph");
    graph.addEventListener("pointerdown", (event) => {
      if (this.locked || this.definition?.source !== "editor") return;
      const [x, y] = this.position(event);
      const points = this.points();
      let index = points.findIndex((p) => Math.hypot(p[0] - x, p[1] - y) < 12);
      if (index < 0) {
        if (
          points.length >= 32 ||
          x <= 0 ||
          x >= 255 ||
          points.some((p) => p[0] === x)
        )
          return;
        index = points.findIndex((p) => p[0] > x);
        points.splice(index, 0, [x, y]);
      }
      this.point = index;
      this.drag = true;
      graph.setPointerCapture(event.pointerId);
      graph.focus();
      this.draw();
      event.preventDefault();
    });
    graph.addEventListener("pointermove", (event) => {
      if (this.drag && !this.locked) this.move(...this.position(event));
    });
    for (const name of ["pointerup", "pointercancel", "lostpointercapture"])
      graph.addEventListener(name, () => {
        this.drag = false;
      });
    graph.addEventListener("keydown", (event) => {
      if (this.locked || this.definition?.source !== "editor") return;
      const p = this.points()[this.point];
      const step = event.shiftKey ? 10 : 1;
      if (event.key === "Delete" || event.key === "Backspace")
        this.deletePoint();
      else if (event.key.startsWith("Arrow"))
        this.move(
          p[0] +
            (event.key === "ArrowRight"
              ? step
              : event.key === "ArrowLeft"
                ? -step
                : 0),
          p[1] +
            (event.key === "ArrowUp"
              ? step
              : event.key === "ArrowDown"
                ? -step
                : 0),
        );
      else return;
      event.preventDefault();
    });
  }
  error(text: string) {
    this.$("status").textContent = text;
  }
  channel(): Channel {
    return this.$("channel").value as Channel;
  }
  points(): Point[] {
    if (this.definition?.source !== "editor")
      throw new Error("Select an editable curve first.");
    return this.definition.points[this.channel()];
  }
  resetDefinition() {
    this.definition =
      this.$("mode").value === "editor"
        ? {
            source: "editor",
            points: Object.fromEntries(
              this.channels.map((c) => [
                c,
                [
                  [0, 0],
                  [255, 255],
                ],
              ]),
            ) as Record<Channel, Point[]>,
          }
        : null;
    this.point = 0;
    this.$("file").value = "";
    this.error("");
  }
  reset() {
    this.editId = null;
    this.$("name").value = "";
    this.$("bin").value = "0";
    this.$("channel").value = "value";
    this.$("mode").value = "editor";
    this.resetDefinition();
    this.draw();
  }
  clear() {
    this.colors = [];
    this.reset();
    this.$("section").open = false;
  }
  edit(id: string) {
    const color = this.colors.find((c) => c.id === id);
    if (!color) return;
    this.editId = id;
    this.definition = JSON.parse(JSON.stringify(color.curve));
    this.$("name").value = color.name;
    this.$("bin").value = String(color.bin);
    this.$("mode").value = color.curve.source;
    this.$("channel").value = "value";
    this.$("file").value = "";
    this.point = 0;
    this.error("");
    this.$("section").open = true;
    this.draw();
    this.$("name").focus();
  }
  remove(id: string) {
    this.colors = this.colors.filter((c) => c.id !== id);
    if (this.editId === id) this.reset();
    this.changed(null, id);
  }
  payload() {
    return this.colors.map(({ id, name, bin, curve }) => ({
      id,
      name,
      bin,
      curve,
    }));
  }
  sample(points: Point[]) {
    let segment = 0;
    return Array.from({ length: 256 }, (_, x) => {
      while (segment < points.length - 2 && x > points[segment + 1][0])
        segment++;
      const [[x0, y0], [x1, y1]] = points.slice(segment, segment + 2);
      return (y0 + ((y1 - y0) * (x - x0)) / (x1 - x0)) / 255;
    });
  }
  samples(): Record<Channel, number[]> {
    const definition = this.definition;
    if (!definition) throw new Error("Import or create a curve first.");
    return definition.source === "gimp"
      ? definition.channels
      : (Object.fromEntries(
          this.channels.map((c) => [c, this.sample(definition.points[c])]),
        ) as Record<Channel, number[]>);
  }
  save() {
    if (this.locked) return;
    const name = this.$("name").value;
    if (
      !/^[A-Za-z0-9][A-Za-z0-9 _-]{0,47}$/.test(name) ||
      name !== name.trim()
    ) {
      this.error(
        "Enter a name using 1 to 48 letters, numbers, spaces, underscores or hyphens.",
      );
      this.$("name").focus();
      return;
    }
    if (!this.definition) {
      this.error("Import a GIMP curve file first.");
      return;
    }
    const compact = (s: string) => s.replaceAll(" ", "").toLowerCase();
    if (
      [
        ...this.builtinNames,
        ...this.colors.filter((c) => c.id !== this.editId).map((c) => c.name),
      ].some((n) => compact(n) === compact(name))
    ) {
      this.error("That name conflicts with another color or package filename.");
      return;
    }
    if (!this.editId && this.colors.length >= 16) {
      this.error("Add at most 16 custom colors per job.");
      return;
    }
    const id =
      this.editId ||
      "custom:" +
        Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) =>
          b.toString(16).padStart(2, "0"),
        ).join("");
    const bin = Number(this.$("bin").value);
    const samples = this.samples();
    const rgb = (["red", "green", "blue"] as const).map((c, i) => {
      const x = samples[c][[180, 150, 90][i]] * 255;
      const low = Math.min(254, Math.floor(x));
      return Math.round(
        255 *
          (samples.value[low] * (1 - (x - low)) +
            samples.value[low + 1] * (x - low)),
      )
        .toString(16)
        .padStart(2, "0");
    });
    const color = {
      id,
      name,
      bin,
      kind: bin ? "natural" : "unnatural",
      family: null,
      swatch: "#" + rgb.join(""),
      curve: JSON.parse(JSON.stringify(this.definition)),
    };
    const index = this.colors.findIndex((c) => c.id === id);
    if (index < 0) this.colors.push(color);
    else this.colors[index] = color;
    this.changed(id, null);
    this.reset();
    this.$("section").open = false;
  }
  position(event: { clientX: number; clientY: number }): Point {
    const box = this.$("graph").getBoundingClientRect();
    return [
      Math.round(
        Math.max(
          0,
          Math.min(255, ((event.clientX - box.left) / box.width) * 300 - 22.5),
        ),
      ),
      Math.round(
        Math.max(
          0,
          Math.min(255, 277.5 - ((event.clientY - box.top) / box.height) * 300),
        ),
      ),
    ];
  }
  move(x: number, y: number) {
    if (!Number.isFinite(x) || !Number.isFinite(y) || this.locked) {
      this.draw();
      return;
    }
    const points = this.points();
    const index = this.point;
    points[index] = [
      index === 0
        ? 0
        : index === points.length - 1
          ? 255
          : Math.max(
              points[index - 1][0] + 1,
              Math.min(points[index + 1][0] - 1, Math.round(x)),
            ),
      Math.max(0, Math.min(255, Math.round(y))),
    ];
    this.draw();
  }
  deletePoint() {
    const points = this.points();
    if (this.point > 0 && this.point < points.length - 1) {
      points.splice(this.point, 1);
      this.point--;
      this.draw();
    }
  }
  setLocked(value: boolean) {
    this.locked = value;
    this.draw();
  }
  draw() {
    const editor = this.definition?.source === "editor";
    this.$("upload").hidden = this.$("mode").value !== "gimp";
    this.$("point-controls").hidden = !editor;
    this.$("file-note").textContent =
      this.definition?.source === "gimp"
        ? `Imported: ${this.definition.filename}. Original samples are preserved.`
        : "Choose a GIMP text curve preset. No particular file extension is required.";
    this.$("save").textContent = this.editId
      ? "Save color changes"
      : "Add color";
    const graph = this.$("graph");
    graph.setAttribute(
      "aria-label",
      editor
        ? `${this.channel()} curve. Add or drag points, or use the numeric fields. Arrow keys move the selected point.`
        : `${this.channel()} imported curve, read only`,
    );
    const svg = (
      tag: string,
      attrs: ArrayLike<unknown> | { [s: string]: unknown },
    ) => {
      const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
      for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
      return el;
    };
    const nodes = [];
    for (let i = 0; i <= 4; i++) {
      const p = 22.5 + (i * 255) / 4;
      nodes.push(
        svg("line", { x1: p, y1: 22.5, x2: p, y2: 277.5, class: "curve-grid" }),
        svg("line", { x1: 22.5, y1: p, x2: 277.5, y2: p, class: "curve-grid" }),
      );
    }
    if (this.definition) {
      const values = this.samples()[this.channel()];
      nodes.push(
        svg("polyline", {
          points: values
            .map((y: number, x: number) => `${x + 22.5},${277.5 - y * 255}`)
            .join(" "),
          fill: "none",
          stroke: {
            value: "currentColor",
            red: "#e77785",
            green: "#62bd86",
            blue: "#78a5ee",
          }[this.channel()],
          "stroke-width": "2",
        }),
      );
      if (editor) {
        const points = this.points();
        points.forEach(([x, y]: any, i: number) =>
          nodes.push(
            svg("circle", {
              cx: x + 22.5,
              cy: 277.5 - y,
              r: i === this.point ? 5 : 3.5,
              class: i === this.point ? "curve-point active" : "curve-point",
            }),
          ),
        );
        this.$("point").replaceChildren(
          ...points.map(([x, y], i) => {
            const o = document.createElement("option");
            o.value = String(i);
            o.textContent = `Point ${i + 1}: ${x} → ${y}`;
            return o;
          }),
        );
        this.$("point").value = String(this.point);
        this.$("input").value = String(points[this.point][0]);
        this.$("output").value = String(points[this.point][1]);
      }
    }
    graph.replaceChildren(...nodes);
    this.$("section")
      .querySelectorAll<HTMLInputElement>("button, input, select")
      .forEach((el) => {
        el.disabled = this.locked;
      });
    if (editor) {
      this.$("input").disabled =
        this.locked ||
        this.point === 0 ||
        this.point === this.points().length - 1;
      this.$("delete-point").disabled =
        this.locked ||
        this.point === 0 ||
        this.point === this.points().length - 1;
      this.$("add-point").disabled = this.locked || this.points().length >= 32;
    }
  }
}
