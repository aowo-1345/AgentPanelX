import { useEffect } from 'react';

interface SilentPollingOptions<T> {
  enabled: boolean;
  intervalMs: number;
  query: (signal: AbortSignal) => Promise<T>;
  onData: (data: T) => void;
}

/** Poll one query without overlapping requests or exposing loading UI. */
export function useSilentPolling<T>({
  enabled,
  intervalMs,
  query,
  onData,
}: SilentPollingOptions<T>) {
  useEffect(() => {
    if (!enabled) return;

    let disposed = false;
    let inFlight = false;
    let runAgain = false;
    let timer: number | null = null;
    let controller: AbortController | null = null;

    function clearTimer() {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
    }

    function schedule(delayMs: number) {
      clearTimer();
      if (disposed || document.visibilityState === 'hidden') return;
      timer = window.setTimeout(() => void run(), delayMs);
    }

    async function run() {
      timer = null;
      if (disposed || document.visibilityState === 'hidden') return;
      if (inFlight) {
        runAgain = true;
        return;
      }

      inFlight = true;
      const requestController = new AbortController();
      controller = requestController;
      try {
        const data = await query(requestController.signal);
        if (!disposed && !requestController.signal.aborted) {
          onData(data);
        }
      } catch {
        // Background refreshes retain the last reliable snapshot and retry later.
      } finally {
        if (controller === requestController) controller = null;
        inFlight = false;
        if (!disposed) {
          const delayMs = runAgain ? 0 : intervalMs;
          runAgain = false;
          schedule(delayMs);
        }
      }
    }

    function refreshNow() {
      clearTimer();
      if (document.visibilityState === 'hidden') {
        controller?.abort();
      } else if (inFlight) {
        runAgain = true;
      } else {
        schedule(0);
      }
    }

    function handleVisibilityChange() {
      refreshNow();
    }

    schedule(intervalMs);
    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('focus', refreshNow);
    window.addEventListener('online', refreshNow);

    return () => {
      disposed = true;
      clearTimer();
      controller?.abort();
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('focus', refreshNow);
      window.removeEventListener('online', refreshNow);
    };
  }, [enabled, intervalMs, onData, query]);
}
