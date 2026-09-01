export async function ensureSession(): Promise<void> {
  const res = await fetch('/v1/ui/session', { method: 'POST', credentials: 'include' });
  if (!res.ok) throw new Error('session failed');
}

export async function getJson<T = Record<string, unknown>>(
  path: string,
): Promise<{ status: number; data: T | null }> {
  const res = await fetch(path, { credentials: 'include' });
  if (!res.ok) {
    return { status: res.status, data: null };
  }
  return { status: res.status, data: (await res.json()) as T };
}

export async function callTool<T = Record<string, unknown>>(
  name: string,
  args: Record<string, unknown> = {},
): Promise<T> {
  const res = await fetch(`/v1/tools/${name}`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  });
  return res.json() as Promise<T>;
}
