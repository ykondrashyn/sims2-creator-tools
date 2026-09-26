// Geometry optimization must not decode or rewrite source textures. Replacing
// the optional image pipeline also avoids its eval requirement under our CSP.
export function getPixels() { throw new Error('Image processing is disabled in the model optimizer.'); }
export const savePixels = getPixels;
