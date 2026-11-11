/** Typed fetch wrapper: same-origin cookies, CSRF header, uniform errors with request id (SEN-04). */

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId: string | null,
    public details?: Record<string, unknown>,
  ) {
    super(message);
  }
}

function cookie(name: string): string | null {
  const m = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return m ? decodeURIComponent(m[1]) : null;
}

export const UNAUTHORIZED_EVENT = 'aastar:unauthorized';

async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  const isForm = body instanceof FormData;            // multipart: the browser sets the boundary header
  if (body !== undefined && !isForm) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') {
    const csrf = cookie('aastar_csrf');
    if (csrf) headers['X-CSRF-Token'] = csrf;
  }
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
      credentials: 'same-origin',
      signal,
    });
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e;
    console.error('[api] network', method, path);
    throw new ApiError(0, 'network_error', 'Cannot reach the server. Check your connection.', null);
  }
  const rid = res.headers.get('x-request-id');
  if (!res.ok) {
    let payload: { error?: { code?: string; message?: string; details?: Record<string, unknown> } } = {};
    try {
      payload = await res.json();
    } catch {
      /* non-JSON error body */
    }
    const err = payload.error ?? {};
    // Breadcrumb for the Hub SDK / Sentinel: status, path and backend request id — never bodies.
    // A 401 from /auth/me just means "not signed in yet" — expected, so not logged.
    if (!(res.status === 401 && path === '/auth/me')) {
      console.error('[api]', res.status, method, path.split('?')[0], rid ?? '');
    }
    if (res.status === 401 && !path.startsWith('/auth/login')) {
      window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT));
    }
    throw new ApiError(res.status, err.code ?? 'http_error', err.message ?? res.statusText, rid, err.details);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>('GET', path, undefined, signal),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {}),
  put: <T>(path: string, body: unknown) => request<T>('PUT', path, body),
  patch: <T>(path: string, body: unknown) => request<T>('PATCH', path, body),
  del: <T>(path: string) => request<T>('DELETE', path),
  upload: <T>(path: string, form: FormData) => request<T>('POST', path, form),
};

export function qs(params: Record<string, string | number | undefined | null | (string | number)[]>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    p.set(k, Array.isArray(v) ? v.join(',') : String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : '';
}
