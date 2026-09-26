import type { RuntimeManifest, SavedJob } from "./package-runtime/types.js";
/* One batch-local setting shared by all package creators. No browser preference. */
export const TextureCompression = {
  mount(
    prefix: string,
    before: HTMLElement | null,
    changed: (...args: any[]) => void,
  ) {
    const root = document.createElement("details");
    root.className = "texture-compression";
    root.id = `${prefix}-compression-options`;
    root.hidden = true;
    const summary = document.createElement("summary");
    summary.textContent = "Advanced options";
    const label = document.createElement("label");
    label.textContent = "Texture compression";
    const select = document.createElement("select");
    select.id = `${prefix}-texture-encoder`;
    let selectedEncoder = "directxtex";
    const names: Record<string, string> = {
      directxtex: "DirectXTex (default)",
      bodyshop: "Body Shop",
      bodyshop_dxt3: "Body Shop (DXT3 only)",
    };
    for (const [value, text] of Object.entries(names)) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = text;
      select.append(option);
    }
    const note = document.createElement("p");
    note.id = `${prefix}-texture-encoder-help`;
    note.className = "build-note";
    select.setAttribute("aria-describedby", note.id);
    label.append(select);
    label.hidden = note.hidden = true;
    const refpackLabel = document.createElement("label");
    refpackLabel.className = "checkbox-label";
    const refpack = document.createElement("input");
    refpack.type = "checkbox";
    refpack.id = `${prefix}-refpack-compression`;
    refpack.checked = true;
    refpack.defaultChecked = true;
    const refpackNote = document.createElement("p");
    refpackNote.id = `${prefix}-refpack-help`;
    refpackNote.className = "build-note";
    refpackNote.textContent =
      "Losslessly compresses data inside generated .package files. The game decompresses it when loading. Texture quality, alpha and mipmaps stay unchanged. Copied mesh files are unchanged.";
    refpack.setAttribute("aria-describedby", refpackNote.id);
    refpackLabel.append(
      refpack,
      document.createTextNode("RefPack package compression"),
    );
    const refpackGroup = document.createElement("div");
    refpackGroup.hidden = true;
    refpackGroup.append(refpackLabel, refpackNote);
    root.append(summary, label, note, refpackGroup);
    if (!before) throw new Error("The compression controls have no parent.");
    before.before(root);
    select.addEventListener("change", () => {
      selectedEncoder = select.value;
      changed("texture");
    });
    refpack.addEventListener("change", () => changed("refpack"));
    return {
      update({
        manifest,
        record,
        formats = [],
        locked = false,
      }: {
        manifest?: RuntimeManifest | null;
        record?: SavedJob | null;
        formats?: string[];
        locked?: boolean;
      }) {
        const release = record?.manifest || manifest;
        refpackGroup.hidden = release?.package_compression?.version !== 1;
        refpack.disabled = locked || refpackGroup.hidden;
        const version = release?.texture_encoders?.version;
        label.hidden = note.hidden = ![1, 2].includes(version);
        root.hidden = label.hidden && refpackGroup.hidden;
        select.disabled = locked || label.hidden;
        const values =
          version === 2
            ? ["directxtex", "bodyshop"]
            : ["directxtex", "bodyshop_dxt3"];
        // Explicit older API settings keep their meaning even in a new engine.
        if (version === 2 && selectedEncoder === "bodyshop_dxt3")
          values.push("bodyshop_dxt3");
        if ([...select.options].map((o) => o.value).join() !== values.join()) {
          select.replaceChildren(
            ...values.map((value) => {
              const option = document.createElement("option");
              option.value = value;
              option.textContent = names[value];
              return option;
            }),
          );
        }
        select.value = values.includes(selectedEncoder)
          ? selectedEncoder
          : "directxtex";
        for (const option of select.options) {
          const supported =
            option.value === "bodyshop" ? ["DXT1", "DXT3", "DXT5"] : ["DXT3"];
          option.disabled =
            option.value !== "directxtex" &&
            !formats.some((f) => supported.includes(f));
        }
        note.textContent =
          version === 2 && selectedEncoder !== "bodyshop_dxt3"
            ? formats.some((f) => ["DXT1", "DXT3", "DXT5"].includes(f))
              ? "Body Shop uses the recovered compressor for DXT1, DXT3 and DXT5 textures. Texture formats stay unchanged."
              : "This batch has no generated DXT1, DXT3 or DXT5 textures. Uncompressed texture formats stay unchanged."
            : formats.includes("DXT3")
              ? "Uses the recovered Body Shop compressor for DXT3 textures. Other formats use DirectXTex."
              : prefix === "tattoo"
                ? "Tattoo textures use DXT5, so DirectXTex handles their compression."
                : "This batch has no generated DXT3 textures. Its textures use DirectXTex or retain their uncompressed format.";
      },
      payload() {
        return {
          ...(label.hidden ? {} : { texture_encoder: select.value }),
          ...(refpackGroup.hidden
            ? {}
            : { refpack_compression: refpack.checked }),
        };
      },
      restore(value: string, refpackValue = true) {
        selectedEncoder = Object.hasOwn(names, value) ? value : "directxtex";
        select.value = selectedEncoder;
        refpack.checked = refpackValue !== false;
      },
      reset() {
        selectedEncoder = select.value = "directxtex";
        refpack.checked = true;
        root.open = false;
      },
    };
  },
};
