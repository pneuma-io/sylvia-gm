/* Two saturating counters, one timer and one IPC in flight. No event objects are retained. */
(() => {
  'use strict';
  let initialized = false;
  window.SylviaLocalDiagnostics = {
    init(bridge) {
      if (initialized || typeof bridge.record_local_fault !== 'function') return;
      initialized = true;
      const categories = ['renderer_error', 'renderer_rejection'];
      const counts = [0, 0];
      const maximumDelta = 65535;
      let inFlight = false;
      let scheduled = false;
      let nextCategory = 0;
      let delay = 1000;

      function schedule() {
        if (inFlight || scheduled || !counts.some(count => count > 0)) return;
        scheduled = true;
        window.setTimeout(drain, delay);
      }

      function settled(success) {
        inFlight = false;
        // Failed/uncertain batches are dropped, never replayed into possibly committed counts.
        delay = success ? 1000 : 30000;
        schedule();
      }

      function drain() {
        scheduled = false;
        const index = counts[nextCategory] > 0 ? nextCategory : 1 - nextCategory;
        const delta = counts[index];
        if (!delta) return;
        counts[index] = 0;
        nextCategory = 1 - index;
        inFlight = true;
        try {
          // An unresolved request stays the sole request, even across further event storms.
          void Promise.resolve(bridge.record_local_fault(categories[index], delta))
            .then(() => settled(true), () => settled(false));
        } catch (_) { settled(false); }
      }

      const record = category => {
        counts[category] = Math.min(maximumDelta, counts[category] + 1);
        schedule();
      };
      window.addEventListener('error', () => record(0));
      window.addEventListener('unhandledrejection', () => record(1));
    },
  };
})();
