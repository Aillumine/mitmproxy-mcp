import type { TrafficRow } from '../poll/drainTraffic';

export const PACKAGE_STORAGE_KEY = 'mitm.attributedPackage';

export type DevicePackage = {
  packageName: string;
  uid: number;
  foreground: boolean;
};

export function parsePackages(result: { packages?: unknown }): DevicePackage[] {
  if (!Array.isArray(result.packages)) return [];
  return result.packages.flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const data = item as Record<string, unknown>;
    if (typeof data.package !== 'string') return [];
    return [
      {
        packageName: data.package,
        uid: typeof data.uid === 'number' ? data.uid : 0,
        foreground: data.foreground === true,
      },
    ];
  });
}

// Rows without a package are hidden while a package is selected: attribution
// runs a second behind the request, so an un-tagged row is either another app's
// or not yet sampled. Showing them would defeat the point of the filter.
//
// 选中应用时，未归属的行一并隐藏：归属比请求慢一拍，没打标的行要么属于别的
// 应用、要么还没采样到。把它们显示出来，这个过滤器就失去意义了。
export function filterRowsByPackage(rows: TrafficRow[], pkg: string | null): TrafficRow[] {
  if (!pkg) return rows;
  return rows.filter((row) => row.package === pkg);
}
