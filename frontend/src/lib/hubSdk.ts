/**
 * DWS Downstream Hub SDK wrapper (PRD SDK-02..05, SDK guide v2). Every call uses the queue form
 * `(window.DwsHub = window.DwsHub || []).push([method, ...args])`, which works before the deferred
 * script has loaded (queued, replayed in order) and after (runs at once). No-op when the SDK tag was not
 * injected. Hub analytics identity is analytics-grade only — never used for access control.
 */

type Queue = { push: (call: unknown[]) => unknown };

declare global {
  interface Window {
    DwsHub?: Queue;
  }
}

export const hubSdkConfigured = Boolean(import.meta.env.VITE_HUB_SDK_KEY && import.meta.env.VITE_HUB_SDK_URL);

function call(method: string, ...args: unknown[]): void {
  if (!hubSdkConfigured) return;
  try {
    (window.DwsHub = window.DwsHub || ([] as unknown[][])).push([method, ...args]);
  } catch {
    /* analytics must never break the app */
  }
}

// Stands in for the guide's `onerror` attribute (inline handlers are blocked by the CSP): after load,
// a DwsHub without the SDK's methods means the script never ran — blocked, or the Hub is unreachable.
if (hubSdkConfigured) {
  window.addEventListener('load', () => {
    if (typeof (window.DwsHub as { identify?: unknown } | undefined)?.identify !== 'function') {
      console.warn('[DwsHub] the Downstream Hub SDK could not be loaded (blocked or unreachable)');
    }
  });
}

export const hub = {
  identify: (id: string | null, email: string) => call('identify', id ? { id, email } : { email }),
  reset: () => call('reset'),
  track: (name: string, props?: Record<string, unknown>) => call('track', name.slice(0, 80), props),
  openBugReport: () => call('openBugReport'),
};
