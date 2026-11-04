/**
 * DWS Downstream Hub SDK wrapper (PRD SDK-02..05). Safe before the script loads: calls are queued
 * on `window.DwsHub` (an array) and replayed by the SDK. No-op when the SDK tag was not injected.
 * Hub analytics identity is analytics-grade only — never used for access control.
 */

type Queue = unknown[][] & { push: (args: unknown[]) => number };
interface DwsHubApi {
  identify(u: { id?: string; email?: string }): void;
  reset(): void;
  track(name: string, props?: Record<string, unknown>): void;
  page(path?: string): void;
  openBugReport(): void;
}

declare global {
  interface Window {
    DwsHub?: DwsHubApi | Queue;
  }
}

export const hubSdkConfigured = Boolean(import.meta.env.VITE_HUB_SDK_KEY && import.meta.env.VITE_HUB_SDK_URL);

function call(method: keyof DwsHubApi, ...args: unknown[]): void {
  if (!hubSdkConfigured) return;
  const hub = window.DwsHub;
  try {
    if (hub && !Array.isArray(hub) && typeof (hub as DwsHubApi)[method] === 'function') {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ((hub as any)[method] as (...a: unknown[]) => void)(...args);
    } else {
      window.DwsHub = (window.DwsHub as Queue) || ([] as unknown as Queue);
      (window.DwsHub as Queue).push([method, ...args]);
    }
  } catch {
    /* analytics must never break the app */
  }
}

export const hub = {
  identify: (id: string | null, email: string) => call('identify', id ? { id, email } : { email }),
  reset: () => call('reset'),
  track: (name: string, props?: Record<string, unknown>) => call('track', name.slice(0, 80), props),
  openBugReport: () => call('openBugReport'),
};
