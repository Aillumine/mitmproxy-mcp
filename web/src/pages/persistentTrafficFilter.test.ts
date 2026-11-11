import { afterEach, describe, expect, it, vi } from 'vitest';
import { loadPersistentTrafficFilter, savePersistentTrafficFilter } from './persistentTrafficFilter';
const legacy = { enabled: true, allow: [], ignore: ['*legacy*'] };
const saved = { enabled: false, allow: ['*api*'], ignore: [] };
afterEach(() => vi.unstubAllGlobals());
describe('persistent display filter', () => {
  it('restores server rules across origins without resurrecting removed rules', async () => {
    vi.stubGlobal('fetch', async () => Response.json({ filter: saved }));
    expect(await loadPersistentTrafficFilter(legacy)).toEqual(saved);
  });
  it('migrates browser rules only when no server file exists', async () => {
    let stored: unknown = null;
    vi.stubGlobal('fetch', async (_url: string, init?: RequestInit) => {
      if (init?.method === 'PUT') stored = JSON.parse(init.body as string);
      return Response.json({ filter: stored });
    });
    expect(await loadPersistentTrafficFilter(legacy)).toEqual(legacy);
    expect(stored).toEqual(legacy);
    await savePersistentTrafficFilter(saved);
    expect(await loadPersistentTrafficFilter(legacy)).toEqual(saved);
  });
  it('never overwrites durable rules when loading fails', async () => {
    vi.stubGlobal('fetch', async (_url: string, init?: RequestInit) => {
      if (init?.method === 'PUT') throw new Error('unexpected write');
      return new Response('', { status: 500 });
    });
    await expect(loadPersistentTrafficFilter(legacy)).rejects.toThrow('读取');
  });
  it('keeps migration available when a new origin has no legacy rules', async () => {
    vi.stubGlobal('fetch', async (_url: string, init?: RequestInit) => {
      if (init?.method === 'PUT') throw new Error('empty origin blocked migration');
      return Response.json({ filter: null });
    });
    expect(await loadPersistentTrafficFilter({ enabled: true, allow: [], ignore: [] }))
      .toEqual({ enabled: true, allow: [], ignore: [] });
  });
  it('surfaces failed saves', async () => {
    vi.stubGlobal('fetch', async () => new Response('', { status: 500 }));
    await expect(savePersistentTrafficFilter(legacy)).rejects.toThrow('保存');
  });
});
