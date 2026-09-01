import { describe, expect, it } from 'vitest';
import {
  drainTraffic,
  mergeTrafficPage,
  shouldRebuildFromFirstPage,
  type TrafficRow,
} from './drainTraffic';

function row(id: string, timestamp: number): TrafficRow {
  return {
    id,
    timestamp,
    method: 'GET',
    url: `https://example.com/${id}`,
    domain: 'example.com',
    status: 200,
    type: 'XHR',
    size: 1,
    time: 1,
    error: null,
  };
}

describe('mergeTrafficPage', () => {
  it('sorts by timestamp ascending and uniques by id', () => {
    const existing = [row('c', 3), row('a', 1)];
    const page = [row('b', 2), row('a', 1)];
    expect(mergeTrafficPage(existing, page).map((item) => item.id)).toEqual([
      'a',
      'b',
      'c',
    ]);
  });

  it('lets the incoming page replace an existing id', () => {
    const existing = [{ ...row('a', 1), status: 200 }];
    const page = [{ ...row('a', 1), status: 500 }];
    const merged = mergeTrafficPage(existing, page);
    expect(merged).toHaveLength(1);
    expect(merged[0]?.status).toBe(500);
  });
});

describe('drainTraffic', () => {
  it('first page reverses newest-first into chronological and does not paginate history', async () => {
    const calls: Array<{ after_id?: string; offset: number }> = [];
    const result = await drainTraffic({
      afterId: null,
      fetchPage: async ({ after_id, offset }) => {
        calls.push({ after_id, offset });
        return {
          success: true,
          requests: [row('c', 3), row('b', 2), row('a', 1)],
          returned: 3,
        };
      },
    });
    expect(calls).toEqual([{ after_id: undefined, offset: 0 }]);
    expect(result.items.map((item) => item.id)).toEqual(['a', 'b', 'c']);
    expect(result.newestId).toBe('c');
  });

  it('incremental keeps after_id fixed and uses offset so middle records are not skipped', async () => {
    const calls: Array<{ after_id?: string; offset: number }> = [];
    const result = await drainTraffic({
      afterId: 'anchor',
      fetchPage: async ({ after_id, offset }) => {
        calls.push({ after_id, offset });
        if (offset === 0) {
          return {
            success: true,
            requests: Array.from({ length: 10 }, (_, i) =>
              row(`n${19 - i}`, 100 + (19 - i)),
            ),
            returned: 10,
          };
        }
        return {
          success: true,
          requests: Array.from({ length: 5 }, (_, i) =>
            row(`n${9 - i}`, 100 + (9 - i)),
          ),
          returned: 5,
        };
      },
    });
    expect(calls).toEqual([
      { after_id: 'anchor', offset: 0 },
      { after_id: 'anchor', offset: 10 },
    ]);
    expect(result.items.map((item) => item.id)).toEqual(
      Array.from({ length: 15 }, (_, i) => `n${5 + i}`),
    );
    expect(result.newestId).toBe('n19');
  });

  it('stops when returned < 10', async () => {
    const calls: Array<{ offset: number }> = [];
    const result = await drainTraffic({
      afterId: 'anchor',
      fetchPage: async ({ offset }) => {
        calls.push({ offset });
        return {
          success: true,
          requests: Array.from({ length: 10 }, (_, i) =>
            row(`n${9 - i}`, 10 + (9 - i)),
          ),
          returned: 5,
        };
      },
    });
    expect(calls).toEqual([{ offset: 0 }]);
    expect(result.items.map((item) => item.id)).toEqual(
      Array.from({ length: 10 }, (_, i) => `n${i}`),
    );
    expect(result.newestId).toBe('n9');
  });

  it('empty incremental keeps previous newestId', async () => {
    const result = await drainTraffic({
      afterId: 'anchor',
      fetchPage: async () => ({ success: true, requests: [], returned: 0 }),
    });
    expect(result.items).toEqual([]);
    expect(result.newestId).toBe('anchor');
  });
});

describe('shouldRebuildFromFirstPage', () => {
  it('rebuilds a new WSS group after clear when the cursor id was reused', () => {
    expect(
      shouldRebuildFromFirstPage({
        afterId: 'ws-4',
        incrementalItems: 0,
        visibleRows: 0,
      }),
    ).toBe(true);
  });

  it('does not rebuild while waiting for the next frame on a populated list', () => {
    expect(
      shouldRebuildFromFirstPage({
        afterId: 'ws-4',
        incrementalItems: 0,
        visibleRows: 4,
      }),
    ).toBe(false);
  });

  it('does not rebuild the first live page that has no cursor yet', () => {
    expect(
      shouldRebuildFromFirstPage({
        afterId: null,
        incrementalItems: 0,
        visibleRows: 0,
      }),
    ).toBe(false);
  });
});
