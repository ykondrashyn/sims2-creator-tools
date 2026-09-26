/** Serialize writes while retaining only the newest save that has not started.
 * Replaced callers await that newest save, including its failure. Running saves
 * keep their own promise so explicit saves and builds can still await persistence.
 */
export function createAutosaveQueue<T>() {
  type Save = {
    run: () => Promise<T>;
    promise: Promise<T>;
    resolve: (value: T) => void;
    reject: (error: unknown) => void;
  };
  let pending: Save | undefined;
  let draining: Promise<void> | undefined;
  let latest: Promise<T> | undefined;

  async function drain() {
    while (pending) {
      const save = pending;
      pending = undefined;
      try {
        save.resolve(await save.run());
      } catch (error) {
        save.reject(error);
      }
    }
    draining = undefined;
  }

  return {
    enqueue(run: () => Promise<T>): Promise<T> {
      if (pending) pending.run = run;
      else {
        let resolve!: Save["resolve"];
        let reject!: Save["reject"];
        const promise = new Promise<T>((yes, no) => {
          resolve = yes;
          reject = no;
        });
        // Autosaves may have no awaiting caller. Keep rejection available to
        // explicit save/flush callers without an unhandled background rejection.
        void promise.catch(() => {});
        pending = { run, promise, resolve, reject };
      }
      latest = pending.promise;
      draining ||= Promise.resolve().then(drain);
      return latest;
    },
    async flush(): Promise<T | undefined> {
      // A save's completion can schedule another save in a promise reaction.
      while (draining) await draining;
      return latest;
    },
  };
}
