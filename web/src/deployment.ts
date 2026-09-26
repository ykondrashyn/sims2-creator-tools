/** All runtime URLs are static files relative to the deployed site's entry page. */
export function runtimeManifestUrl(): string {
  const configured = document.querySelector<HTMLMetaElement>(
    'meta[name="runtime-manifest"]',
  )?.content;
  return new URL(configured || "manifest.json", document.baseURI).href;
}
