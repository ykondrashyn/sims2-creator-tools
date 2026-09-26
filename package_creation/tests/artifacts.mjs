import path from 'node:path';
import {existsSync} from 'node:fs';
export function artifact(name, supplied) {
  const value = supplied || process.env[name];
  if (!value || !existsSync(value)) throw new Error(`Required artifact ${name} is missing. Run python -m tools.project verify.`);
  return path.resolve(value);
}
export const fixtures = path.resolve(process.env.PROJECT_FIXTURE_ROOT || process.cwd());
export function fixturePath(value) {
  if (!path.isAbsolute(value)) return path.join(fixtures, value);
  const marker = '/ts_proj/';
  if (!value.includes(marker)) throw new Error('Unregistered absolute fixture path: ' + value);
  return path.join(fixtures, value.split(marker)[1]);
}
