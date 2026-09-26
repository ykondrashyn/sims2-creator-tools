import { optimize, VERSION } from './optimizer.mjs';
globalThis.onmessage = async ({data}) => {
  const {id, job, revision} = data, context = {id, job, revision, version:VERSION};
  try {
    if (data.version !== VERSION) throw new Error('Unsupported optimizer version. Reload the website.');
    const result = await optimize(new Uint8Array(data.bytes), data.format, data.options,
      progress => postMessage({...context, progress}));
    postMessage({...context, result}, [result.bytes.buffer]);
  } catch (e) {
    postMessage({...context, error:{message:e.message || 'Model optimization failed. The original file is unchanged.'}});
  }
};
