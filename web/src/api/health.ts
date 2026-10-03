export interface Health {
  status: string;
}

/** Calls the liveness endpoint. Throws on a network failure or a non-2xx answer. */
export async function fetchHealth(signal?: AbortSignal): Promise<Health> {
  const response = await fetch("/api/v1/healthz", signal ? { signal } : {});
  if (!response.ok) throw new Error(`healthz answered ${String(response.status)}`);
  return (await response.json()) as Health;
}
