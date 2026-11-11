import { parseStoredTrafficFilter, type TrafficDisplayFilter } from './trafficFilter';

const endpoint = '/v1/ui/traffic-filter';

export async function savePersistentTrafficFilter(filter: TrafficDisplayFilter): Promise<void> {
  const response = await fetch(endpoint, {
    method: 'PUT',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(filter),
  });
  if (!response.ok) throw new Error('过滤规则保存失败，请重试');
}

export async function loadPersistentTrafficFilter(
  legacy: TrafficDisplayFilter,
): Promise<TrafficDisplayFilter> {
  const response = await fetch(endpoint, { credentials: 'include' });
  if (!response.ok) throw new Error('过滤规则读取失败，请刷新重试');
  const data = await response.json() as { filter: TrafficDisplayFilter | null };
  // Server state wins, including empty lists after removal. Migrate browser
  // rules only once, so stale caches cannot resurrect deleted ignore rules.
  //
  // 以服务端为准，包括主动清空后的列表。仅在文件不存在时迁移浏览器规则，
  // 避免旧缓存把已删除的忽略项重新加回来。
  if (data.filter === null) {
    if (legacy.allow.length || legacy.ignore.length || !legacy.enabled) {
      await savePersistentTrafficFilter(legacy);
    }
    return legacy;
  }
  return parseStoredTrafficFilter(data.filter);
}
