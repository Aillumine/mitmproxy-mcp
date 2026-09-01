import { json } from '@codemirror/lang-json';
import { oneDark } from '@codemirror/theme-one-dark';
import CodeMirror from '@uiw/react-codemirror';
import { useEffect, useMemo, useState } from 'react';
import JsonView from 'react18-json-view';
import 'react18-json-view/src/style.css';
import 'react18-json-view/src/dark.css';
import { prettyLooseJson } from '../format';

type JsonPaneProps = {
  raw: string;
};

export function JsonPane({ raw }: JsonPaneProps) {
  const parsed = useMemo(() => prettyLooseJson(raw), [raw]);
  const [formatted, setFormatted] = useState(true);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setFormatted(true);
  }, [raw]);

  if (!parsed.ok) {
    return <pre className="mono json-pane-fallback">{parsed.text}</pre>;
  }

  const pretty = parsed.text;
  const value = parsed.value;
  const prefix = parsed.prefix;

  async function copy() {
    try {
      await navigator.clipboard.writeText(pretty);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="json-pane">
      <div className="json-pane-toolbar">
        <button
          type="button"
          aria-pressed={formatted}
          onClick={() => setFormatted((on) => !on)}
        >
          {formatted ? 'Tree' : 'Format'}
        </button>
        <button type="button" onClick={() => void copy()}>
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      {formatted ? (
        <CodeMirror
          value={pretty}
          readOnly
          editable={false}
          theme={oneDark}
          extensions={prefix ? [] : [json()]}
          basicSetup={{
            highlightActiveLine: false,
            highlightActiveLineGutter: false,
          }}
        />
      ) : (
        <>
          {prefix ? <p className="mono muted json-pane-prefix">{prefix}</p> : null}
          <JsonView
            src={value}
            theme="a11y"
            dark
            collapsed={2}
            enableClipboard={false}
          />
        </>
      )}
    </div>
  );
}
