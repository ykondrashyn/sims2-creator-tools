export function outputExtension(format: string) {
  if (format === "Png") return "png";
  if (format === "Jpeg") return "jpg";
  if (format === "WebP") return "webp";
  throw new Error("The model returned an unsupported image format.");
}
export function outputFilename(name: string, model: string, extension: string) {
  const stem =
    name
      .replace(/\.[^.]*$/, "")
      .replace(/[^A-Za-z0-9._-]+/g, "_")
      .replace(/^[._]+|[._]+$/g, "")
      .slice(0, 96) || "image";
  return `${stem}_${model}_upscaled.${extension}`;
}
