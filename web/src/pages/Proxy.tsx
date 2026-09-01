import { useCallback, useEffect, useState } from 'react';
import { callTool } from '../api';
import { certDisplayText } from './proxyState';

type CertResult = Record<string, unknown> & {
  success?: boolean;
  message?: string;
  install_instructions?: string;
};

export default function Proxy() {
  const [text, setText] = useState('');
  const [banner, setBanner] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const refresh = useCallback(async () => {
    const result = await callTool<CertResult>('get_cert_info');
    setText(certDisplayText(result));
    setBanner(result.success === false ? (result.message ?? 'get_cert_info failed') : null);
    setLoaded(true);
  }, []);

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

  return (
    <div className="proxy">
      <div className="page-toolbar">
        <button type="button" onClick={() => void refresh()}>
          刷新
        </button>
      </div>
      {banner ? <p className="page-banner warn-banner">{banner}</p> : null}
      <pre className="mono proxy-cert">{loaded ? text : 'Loading…'}</pre>
    </div>
  );
}
