export const IOS_POLL_MS = 3000;
export const EMPTY_IOS_MESSAGE = '没有 iOS 设备';

export type IosTab = 'all' | 'simulators' | 'devices';

export type IosDevice = {
  udid: string;
  name: string;
  state: string;
  osVersion: string;
  isSimulator: boolean;
};

export type ToolEnvelope = {
  success?: boolean;
  message?: string;
};

export const IOS_TABS: { id: IosTab; label: string; tool: string }[] = [
  { id: 'all', label: '全部', tool: 'ios_list_devices' },
  { id: 'simulators', label: '模拟器', tool: 'ios_list_simulators' },
  { id: 'devices', label: '真机', tool: 'ios_list_real_devices' },
];

export function listToolForTab(tab: IosTab): string {
  const found = IOS_TABS.find((item) => item.id === tab);
  return found?.tool ?? 'ios_list_devices';
}

export function emptyMessage(tab: IosTab): string {
  if (tab === 'simulators') return '没有 iOS 模拟器';
  if (tab === 'devices') return '没有 iOS 真机';
  return EMPTY_IOS_MESSAGE;
}

function inferSimulator(rec: Record<string, unknown>, tab: IosTab): boolean {
  if (tab === 'simulators') return true;
  if (tab === 'devices') return false;
  if (rec.is_simulator === true) return true;
  if (rec.device_type === 'simulator') return true;
  return false;
}

export function parseDevices(result: { devices?: unknown }, tab: IosTab): IosDevice[] {
  if (!Array.isArray(result.devices)) return [];
  const out: IosDevice[] = [];
  for (const item of result.devices) {
    if (!item || typeof item !== 'object') continue;
    const rec = item as Record<string, unknown>;
    if (typeof rec.udid !== 'string' || !rec.udid) continue;
    out.push({
      udid: rec.udid,
      name: typeof rec.name === 'string' ? rec.name : '',
      state: typeof rec.state === 'string' ? rec.state : '',
      osVersion: typeof rec.os_version === 'string' ? rec.os_version : '',
      isSimulator: inferSimulator(rec, tab),
    });
  }
  return out;
}

export function isSelectedPresent(devices: IosDevice[], udid: string | null): boolean {
  return udid != null && devices.some((device) => device.udid === udid);
}

export function selectedDevice(devices: IosDevice[], udid: string | null): IosDevice | undefined {
  if (udid == null) return undefined;
  return devices.find((device) => device.udid === udid);
}

export function showSimulatorActions(device: IosDevice | undefined): boolean {
  return device?.isSimulator === true;
}

export function udidArgs(udid: string): { udid: string } {
  return { udid };
}

export function resultToJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}
