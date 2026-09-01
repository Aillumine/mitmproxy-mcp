export type TrafficRow = {
  id: string;
  timestamp: number;
  method: string;
  url: string;
  domain: string;
  status: number;
  type: string;
  size: number;
  time: number;
  error: string | null;
};

export type TrafficListPage = {
  success: boolean;
  requests?: TrafficRow[];
  returned?: number;
};

const PAGE_SIZE = 10;

export function mergeTrafficPage(
  existing: TrafficRow[],
  page: TrafficRow[],
): TrafficRow[] {
  const byId = new Map<string, TrafficRow>();
  for (const row of existing) byId.set(row.id, row);
  for (const row of page) byId.set(row.id, row);
  return [...byId.values()].sort(
    (a, b) => a.timestamp - b.timestamp || a.id.localeCompare(b.id),
  );
}

export async function drainTraffic(options: {
  afterId: string | null;
  fetchPage: (args: {
    after_id?: string;
    limit: number;
    offset: number;
  }) => Promise<TrafficListPage>;
}): Promise<{ items: TrafficRow[]; newestId: string | null }> {
  let items: TrafficRow[] = [];
  const firstPageOnly = options.afterId === null;
  let offset = 0;

  while (true) {
    const page = await options.fetchPage({
      ...(options.afterId ? { after_id: options.afterId } : {}),
      limit: PAGE_SIZE,
      offset,
    });
    const rows = page.success ? page.requests ?? [] : [];
    items = mergeTrafficPage(items, rows);
    if (firstPageOnly) break;
    const returned = page.returned ?? rows.length;
    if (returned < PAGE_SIZE) break;
    offset += PAGE_SIZE;
  }

  const newest = items[items.length - 1];
  return {
    items,
    newestId: newest?.id ?? options.afterId,
  };
}

/**
 * 列表已被清空（Clear / 重启后 ID 从 req-1 重来），但增量 cursor
 * 仍指着库里同名的新记录时，after_id 会把新握手全部滤掉。
 * 这时应按第一页重建分组，而不是继续等「比 cursor 更新」的帧。
 */
export function shouldRebuildFromFirstPage(options: {
  afterId: string | null;
  incrementalItems: number;
  visibleRows: number;
}): boolean {
  return (
    options.afterId != null &&
    options.incrementalItems === 0 &&
    options.visibleRows === 0
  );
}
