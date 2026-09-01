import { describe, expect, it } from 'vitest';
import {
  EMPTY_IOS_MESSAGE,
  IOS_POLL_MS,
  emptyMessage,
  isSelectedPresent,
  listToolForTab,
  parseDevices,
  resultToJson,
  selectedDevice,
  showSimulatorActions,
  udidArgs,
} from './iosState';

describe('constants', () => {
  it('polls devices every 3s and uses the empty-state copy', () => {
    expect(IOS_POLL_MS).toBe(3000);
    expect(EMPTY_IOS_MESSAGE).toBe('没有 iOS 设备');
    expect(emptyMessage('all')).toBe('没有 iOS 设备');
    expect(emptyMessage('simulators')).toBe('没有 iOS 模拟器');
    expect(emptyMessage('devices')).toBe('没有 iOS 真机');
  });
});

describe('listToolForTab', () => {
  it('maps tabs to ios list tools', () => {
    expect(listToolForTab('all')).toBe('ios_list_devices');
    expect(listToolForTab('simulators')).toBe('ios_list_simulators');
    expect(listToolForTab('devices')).toBe('ios_list_real_devices');
  });
});

describe('parseDevices', () => {
  it('maps udid rows from ios_list_devices', () => {
    expect(
      parseDevices(
        {
          devices: [
            {
              udid: 'SIM-1',
              name: 'iPhone 16',
              state: 'Booted',
              os_version: '18.0',
              is_simulator: true,
              device_type: 'simulator',
            },
            {
              udid: 'PHONE-1',
              name: 'Jorim iPhone',
              state: 'available',
              is_simulator: false,
              device_type: 'device',
            },
          ],
        },
        'all',
      ),
    ).toEqual([
      { udid: 'SIM-1', name: 'iPhone 16', state: 'Booted', osVersion: '18.0', isSimulator: true },
      {
        udid: 'PHONE-1',
        name: 'Jorim iPhone',
        state: 'available',
        osVersion: '',
        isSimulator: false,
      },
    ]);
  });

  it('treats simulator-tab rows as simulators even without is_simulator', () => {
    const rows = parseDevices(
      { devices: [{ udid: 'SIM-2', name: 'iPhone SE', state: 'Shutdown' }] },
      'simulators',
    );
    expect(rows[0]?.isSimulator).toBe(true);
  });

  it('treats real-device-tab rows as devices even if is_simulator is missing', () => {
    const rows = parseDevices({ devices: [{ udid: 'PHONE-2', name: 'iPhone' }] }, 'devices');
    expect(rows[0]?.isSimulator).toBe(false);
  });

  it('skips invalid rows and empty lists without throwing', () => {
    expect(parseDevices({}, 'all')).toEqual([]);
    expect(parseDevices({ devices: [{ name: 'no udid' }, null, 'x'] }, 'all')).toEqual([]);
    expect(parseDevices({ devices: [] }, 'simulators')).toEqual([]);
  });

  it('infers simulator from device_type when is_simulator is absent', () => {
    const rows = parseDevices(
      { devices: [{ udid: 'SIM-3', device_type: 'simulator' }] },
      'all',
    );
    expect(rows[0]?.isSimulator).toBe(true);
  });
});

describe('selection and simulator actions', () => {
  const devices = [
    { udid: 'SIM-1', name: 'iPhone 16', state: 'Booted', osVersion: '18.0', isSimulator: true },
    { udid: 'PHONE-1', name: 'iPhone', state: 'available', osVersion: '', isSimulator: false },
  ];

  it('clears selection when the udid leaves the list', () => {
    expect(isSelectedPresent(devices, 'SIM-1')).toBe(true);
    expect(isSelectedPresent(devices, 'gone')).toBe(false);
    expect(isSelectedPresent(devices, null)).toBe(false);
  });

  it('shows boot/shutdown only for simulators', () => {
    expect(showSimulatorActions(selectedDevice(devices, 'SIM-1'))).toBe(true);
    expect(showSimulatorActions(selectedDevice(devices, 'PHONE-1'))).toBe(false);
    expect(showSimulatorActions(selectedDevice(devices, null))).toBe(false);
  });
});

describe('tool args use udid not serial', () => {
  it('builds udid-only args', () => {
    expect(udidArgs('AAAA-BBBB')).toEqual({ udid: 'AAAA-BBBB' });
    expect(udidArgs('AAAA-BBBB')).not.toHaveProperty('serial');
  });
});

describe('result display', () => {
  it('pretty-prints tool JSON', () => {
    expect(resultToJson({ success: true, udid: 'SIM-1' })).toBe(
      '{\n  "success": true,\n  "udid": "SIM-1"\n}',
    );
  });
});
