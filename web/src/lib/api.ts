/**
 * The one place that talks HTTP to the API (SPEC §5.4, §12).
 *
 * - cookies always travel (`credentials: "include"`);
 * - unsafe methods carry `X-CSRF-Token` read from the `baton_csrf` cookie;
 * - a non-2xx answer becomes a typed `ApiError` parsed from problem+json;
 * - 401 sends the user to /login and keeps the return path;
 * - 403 CSRF_FAILED refetches /me (which re-issues the cookie) and retries once.
 */
import createClient from "openapi-fetch";

import type { components, paths } from "../api/generated";

export const API_BASE = "";
const CSRF_COOKIE = "baton_csrf";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export type ItemOut = components["schemas"]["ItemOut"];

/** The problem+json body of SPEC §12, plus what the client needs to act on it. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: string;
  readonly title: string;
  readonly requestId: string | null;
  readonly errors: { field: string; message: string; type?: string }[] | undefined;
  readonly current: ItemOut | undefined;
  readonly changesSince: Record<string, unknown>[] | undefined;
  readonly retryAfter: number | undefined;
  /** The whole body, for extension members such as who claimed an item. */
  readonly body: Record<string, unknown>;

  constructor(init: {
    status: number;
    code: string;
    detail: string;
    title?: string | undefined;
    requestId?: string | null | undefined;
    errors?: ApiError["errors"];
    current?: ItemOut | undefined;
    changesSince?: Record<string, unknown>[] | undefined;
    retryAfter?: number | undefined;
    body?: Record<string, unknown> | undefined;
  }) {
    super(init.detail);
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code;
    this.detail = init.detail;
    this.title = init.title ?? init.detail;
    this.requestId = init.requestId ?? null;
    this.errors = init.errors;
    this.current = init.current;
    this.changesSince = init.changesSince;
    this.retryAfter = init.retryAfter;
    this.body = init.body ?? {};
  }
}

/** The request never produced an answer (offline, connection reset, response dropped). */
export class NetworkError extends Error {
  constructor(cause: unknown) {
    super("Couldn't reach Baton.", { cause });
    this.name = "NetworkError";
  }
}

export function isRetryable(error: unknown): boolean {
  if (error instanceof NetworkError) return true;
  return error instanceof ApiError && error.status === 503 && error.code === "BUSY";
}

/** The members of a problem+json body the client reads. All optional: a proxy may send HTML. */
interface Problem {
  code?: string;
  detail?: string;
  title?: string;
  request_id?: string | null;
  errors?: ApiError["errors"];
  current?: ItemOut;
  changes_since?: Record<string, unknown>[];
}

/** Turns any non-2xx response into an ApiError. Tolerates bodies that aren't problem+json. */
export async function parseApiError(response: Response): Promise<ApiError> {
  let body: Problem & Record<string, unknown> = {};
  try {
    const parsed: unknown = await response.json();
    if (parsed && typeof parsed === "object") body = parsed as typeof body;
  } catch {
    // An HTML error page from a proxy, or an empty body. Fall through to generic values.
  }
  const retryAfterHeader = Number(response.headers.get("Retry-After"));
  return new ApiError({
    status: response.status,
    code: body.code ?? (response.status >= 500 ? "INTERNAL" : "UNKNOWN"),
    detail: body.detail ?? (response.statusText || "Request failed."),
    title: body.title ?? response.statusText,
    requestId: body.request_id ?? response.headers.get("X-Request-ID"),
    errors: Array.isArray(body.errors) ? body.errors : undefined,
    current: body.current,
    changesSince: Array.isArray(body.changes_since) ? body.changes_since : undefined,
    retryAfter:
      Number.isFinite(retryAfterHeader) && retryAfterHeader > 0 ? retryAfterHeader : undefined,
    body,
  });
}

export function readCookie(name: string, source: string = document.cookie): string | null {
  for (const part of source.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return decodeURIComponent(rest.join("="));
  }
  return null;
}

export function isUnsafe(method: string): boolean {
  return !SAFE_METHODS.has(method.toUpperCase());
}

let unauthenticatedHandler: (() => void) | null = null;
/** Called on a 401 from anywhere except the login call itself. Set once by the app. */
export function onUnauthenticated(handler: (() => void) | null): void {
  unauthenticatedHandler = handler;
}

async function refreshCsrf(): Promise<void> {
  await fetch(`${API_BASE}/api/v1/me`, { credentials: "include" });
}

/** `fetch` with Baton's rules applied. Throws ApiError / NetworkError; resolves only for 2xx. */
export async function batonFetch(input: Request): Promise<Response> {
  const send = async (request: Request): Promise<Response> => {
    const prepared = request.clone();
    if (isUnsafe(prepared.method)) {
      const token = readCookie(CSRF_COOKIE);
      if (token) prepared.headers.set("X-CSRF-Token", token);
    }
    try {
      return await fetch(new Request(prepared, { credentials: "include" }));
    } catch (error) {
      throw new NetworkError(error);
    }
  };

  let response = await send(input);
  if (response.status === 403 && isUnsafe(input.method)) {
    const peek = await parseApiError(response.clone());
    if (peek.code === "CSRF_FAILED") {
      await refreshCsrf();
      response = await send(input);
    }
  }
  if (response.ok) return response;

  const error = await parseApiError(response);
  if (error.status === 401 && !new URL(input.url).pathname.endsWith("/auth/login")) {
    unauthenticatedHandler?.();
  }
  throw error;
}

export const api = createClient<paths>({ baseUrl: API_BASE, fetch: batonFetch });

/** Unwraps an openapi-fetch result. Errors were already thrown by `batonFetch`. */
export async function unwrap<T>(result: Promise<{ data?: T | undefined }>): Promise<T> {
  const { data } = await result;
  if (data === undefined) {
    throw new ApiError({ status: 500, code: "INTERNAL", detail: "The server sent no content." });
  }
  return data;
}
