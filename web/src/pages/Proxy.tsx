import { useCallback, useEffect, useState } from 'react';
import { callTool } from '../api';
import {
  THROTTLE_LABELS,
  THROTTLE_PROFILES,
  certDisplayText,
  formatThrottleSummary,
  normalizeThrottleProfile,
  type ThrottleInfo,
  type ThrottleProfile,
} from './proxyState';

type CertResult = Record<string, unknown> & {
  success?: boolean;
  message?: string;
  install_instructions?: string;
};

export default function Proxy() {
  const [text, setText] = useState('');
  const [banner, setBanner] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [throttle, setThrottle] = useState<ThrottleProfile>('off');
  const [throttleSummary, setThrottleSummary] = useState('弱网：关闭');
  const [throttleBusy, setThrottleBusy] = useState(false);

  const applyThrottleResult = useCallback((result: ThrottleInfo) => {
    const profile = normalizeThrottleProfile(result.profile);
    setThrottle(profile);
    setThrottleSummary(formatThrottleSummary(result));
  }, []);

  const refresh = useCallback(async () => {
    const [cert, throttleResult] = await Promise.all([
      callTool<CertResult>('get_cert_info'),
      callTool<ThrottleInfo>('throttle_get'),
    ]);
    setText(certDisplayText(cert));
    applyThrottleResult(throttleResult);
    const errors = [
      cert.success === false ? (cert.message ?? 'get_cert_info failed') : null,
      throttleResult.success === false
        ? (throttleResult.message ?? 'throttle_get failed')
        : null,
    ].filter(Boolean);
    setBanner(errors.length ? errors.join('\n') : null);
    setLoaded(true);
  }, [applyThrottleResult]);

  useEffect(() => {
    let cancelled = false;
    void refresh().catch((err) => {
      if (!cancelled) {
        setBanner(err instanceof Error ? err.message : 'get_cert_info failed');
        setLoaded(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  const onSelectThrottle = async (profile: ThrottleProfile) => {
    if (throttleBusy || profile === throttle) return;
    setThrottleBusy(true);
    setBanner(null);
    try {
      const result = await callTool<ThrottleInfo>('throttle_set', { profile });
      applyThrottleResult(result);
      if (result.success === false) {
        setBanner(result.message ?? 'throttle_set failed');
      } else if (result.message) {
        setBanner(result.message);
      }
    } catch (err) {
      setBanner(err instanceof Error ? err.message : 'throttle_set failed');
    } finally {
      setThrottleBusy(false);
    }
  };

  return (
    <div className="proxy">
      <div className="page-toolbar">
        <button type="button" onClick={() => void refresh()} disabled={throttleBusy}>
          刷新
        </button>
      </div>
      <section className="proxy-throttle" aria-label="弱网模拟">
        <div className="proxy-throttle-head">
          <h2>弱网模拟</h2>
          <p className="proxy-throttle-summary mono">{throttleSummary}</p>
        </div>
        <div className="proxy-throttle-options" role="group" aria-label="弱网档位">
          {THROTTLE_PROFILES.map((profile) => (
            <button
              key={profile}
              type="button"
              className={profile === throttle ? 'primary' : undefined}
              aria-pressed={profile === throttle}
              disabled={throttleBusy}
              onClick={() => void onSelectThrottle(profile)}
            >
              {THROTTLE_LABELS[profile]}
            </button>
          ))}
        </div>
        <p className="proxy-throttle-hint">
          4G ≈ 20ms / ↓4Mbps ↑3Mbps · 3G ≈ 100ms / ↓750kbps ↑250kbps · 2G ≈ 300ms /
          ↓50kbps ↑20kbps。代理运行中切换立即生效。
        </p>
      </section>
      {banner ? <p className="page-banner warn-banner">{banner}</p> : null}
      <pre className="mono proxy-cert">{loaded ? text : 'Loading…'}</pre>
    </div>
  );
}
