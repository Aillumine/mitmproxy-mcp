import { describe, expect, it } from 'vitest';
import {
  ANDROID_POLL_MS,
  EMPTY_ADB_MESSAGE,
  injectFailureMessage,
  isSelectedPresent,
  mergeDeviceLists,
  mergeDeviceModel,
  modelFromInfo,
  needsDeviceInfo,
  parseDevices,
  resolveAndroidProxyPort,
  resultToJson,
  reverseProxyArgs,
  serialArgs,
  setupWifiProxyArgs,
  wifiProxyPort,
} from './androidState';

describe('constants', () => {
  it('polls devices every 3s and uses the empty-state copy', () => {
    expect(ANDROID_POLL_MS).toBe(3000);
    expect(EMPTY_ADB_MESSAGE).toBe('没有 adb 设备');
  });
});

describe('parseDevices', () => {
  it('maps serial, state, and model from list', () => {
    expect(
      parseDevices({
        devices: [
          { serial: 'emulator-5554', state: 'device', model: 'sdk_gphone64_arm64' },
          { serial: 'ABC123', state: 'offline' },
        ],
      }),
    ).toEqual([
      { serial: 'emulator-5554', state: 'device', model: 'sdk_gphone64_arm64' },
      { serial: 'ABC123', state: 'offline', model: '' },
    ]);
  });

  it('skips invalid rows and empty lists', () => {
    expect(parseDevices({})).toEqual([]);
    expect(parseDevices({ devices: [{ state: 'device' }, null, 'x'] })).toEqual([]);
  });
});

describe('device info fallback', () => {
  it('needs get_device_info when list has no model', () => {
    expect(needsDeviceInfo({ serial: 's1', state: 'device', model: '' })).toBe(true);
    expect(needsDeviceInfo({ serial: 's1', state: 'device', model: 'Pixel 8' })).toBe(false);
  });

  it('reads model from android_get_device_info and merges into the table', () => {
    expect(modelFromInfo({ device: { model: 'Pixel 8' } })).toBe('Pixel 8');
    expect(modelFromInfo({})).toBe('');
    const merged = mergeDeviceModel(
      [{ serial: 's1', state: 'device', model: '' }],
      's1',
      'Pixel 8',
    );
    expect(merged[0]?.model).toBe('Pixel 8');
  });

  it('keeps a fetched model across list polls that omit it', () => {
    const merged = mergeDeviceLists(
      [{ serial: 's1', state: 'device', model: 'Pixel 8' }],
      [{ serial: 's1', state: 'device', model: '' }],
    );
    expect(merged[0]?.model).toBe('Pixel 8');
  });
});

describe('selection', () => {
  it('clears selection when the serial leaves the list', () => {
    const devices = [{ serial: 's1', state: 'device', model: '' }];
    expect(isSelectedPresent(devices, 's1')).toBe(true);
    expect(isSelectedPresent(devices, 'gone')).toBe(false);
    expect(isSelectedPresent(devices, null)).toBe(false);
  });
});

describe('tool args use serial not udid', () => {
  it('builds serial-only args', () => {
    expect(serialArgs('emulator-5554')).toEqual({ serial: 'emulator-5554' });
    expect(serialArgs('emulator-5554')).not.toHaveProperty('udid');
  });

  it('passes current proxy port to reverse tools', () => {
    expect(reverseProxyArgs('s1', 9999)).toEqual({ serial: 's1', port: 9999 });
  });

  it('maps Wi-Fi form to android_setup_proxy phone fields', () => {
    expect(setupWifiProxyArgs('s1', '192.168.1.8', 8888)).toEqual({
      serial: 's1',
      proxy_host: '192.168.1.8',
      proxy_port: 8888,
    });
  });
});

describe('resolveAndroidProxyPort', () => {
  it('uses the running runtime port, otherwise the topbar input', () => {
    expect(resolveAndroidProxyPort(true, 9999, 8888)).toBe(9999);
    expect(resolveAndroidProxyPort(false, 9999, 8888)).toBe(8888);
  });
});

describe('wifiProxyPort', () => {
  it('parses a positive port or falls back', () => {
    expect(wifiProxyPort('8080', 8888)).toBe(8080);
    expect(wifiProxyPort('', 8888)).toBe(8888);
    expect(wifiProxyPort('0', 8888)).toBe(8888);
  });
});

describe('result display', () => {
  it('pretty-prints tool JSON', () => {
    expect(resultToJson({ success: true, proxy: '127.0.0.1:8888' })).toBe(
      '{\n  "success": true,\n  "proxy": "127.0.0.1:8888"\n}',
    );
  });

  it('surfaces android_inject_system_cert failure message', () => {
    expect(injectFailureMessage({ success: false, message: 'need root' })).toBe('need root');
    expect(injectFailureMessage({ success: false })).toBe('android_inject_system_cert failed');
    expect(injectFailureMessage({ success: true })).toBeNull();
  });
});
