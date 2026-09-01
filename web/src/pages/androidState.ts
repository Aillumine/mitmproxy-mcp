export const ANDROID_POLL_MS = 3000;
export const EMPTY_ADB_MESSAGE = '没有 adb 设备';

export type AndroidDevice = {
  serial: string;
  state: string;
  model: string;
};

export type ToolEnvelope = {
  success?: boolean;
  message?: string;
};

export function parseDevices(result: { devices?: unknown }): AndroidDevice[] {
  if (!Array.isArray(result.devices)) return [];
  const out: AndroidDevice[] = [];
  for (const item of result.devices) {
    if (!item || typeof item !== 'object') continue;
    const rec = item as Record<string, unknown>;
    if (typeof rec.serial !== 'string' || !rec.serial) continue;
    out.push({
      serial: rec.serial,
      state: typeof rec.state === 'string' ? rec.state : '',
      model: typeof rec.model === 'string' ? rec.model : '',
    });
  }
  return out;
}

export function needsDeviceInfo(device: AndroidDevice): boolean {
  return device.model.trim() === '';
}

export function modelFromInfo(result: { device?: { model?: unknown } }): string {
  return typeof result.device?.model === 'string' ? result.device.model : '';
}

export function mergeDeviceModel(
  devices: AndroidDevice[],
  serial: string,
  model: string,
): AndroidDevice[] {
  if (!model) return devices;
  return devices.map((device) => (device.serial === serial ? { ...device, model } : device));
}

export function mergeDeviceLists(prev: AndroidDevice[], next: AndroidDevice[]): AndroidDevice[] {
  const prevBySerial = new Map(prev.map((device) => [device.serial, device]));
  return next.map((device) => {
    if (device.model) return device;
    const old = prevBySerial.get(device.serial);
    return old?.model ? { ...device, model: old.model } : device;
  });
}

export function isSelectedPresent(devices: AndroidDevice[], serial: string | null): boolean {
  return serial != null && devices.some((device) => device.serial === serial);
}

export function serialArgs(serial: string): { serial: string } {
  return { serial };
}

export function reverseProxyArgs(serial: string, port: number): { serial: string; port: number } {
  return { serial, port };
}

export function setupWifiProxyArgs(
  serial: string,
  host: string,
  port: number,
): { serial: string; proxy_host: string; proxy_port: number } {
  return { serial, proxy_host: host, proxy_port: port };
}

export function wifiProxyPort(input: string, fallback: number): number {
  const parsed = Number(input);
  return Number.isFinite(parsed) && parsed > 0 ? Math.trunc(parsed) : fallback;
}

export function resultToJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

export function injectFailureMessage(result: ToolEnvelope): string | null {
  if (result.success !== false) return null;
  return result.message ?? 'android_inject_system_cert failed';
}

export function resolveAndroidProxyPort(
  running: boolean,
  runtimePort: number,
  portInput: number,
): number {
  return running ? runtimePort : portInput;
}
