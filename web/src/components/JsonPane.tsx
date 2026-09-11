import { json } from '@codemirror/lang-json';
import { oneDark } from '@codemirror/theme-one-dark';
import { Decoration, EditorView, MatchDecorator, ViewPlugin, type DecorationSet, type ViewUpdate } from '@codemirror/view';
import CodeMirror from '@uiw/react-codemirror';
import { useEffect, useMemo, useState } from 'react';
import JsonView from 'react18-json-view';
import 'react18-json-view/src/style.css';
import 'react18-json-view/src/dark.css';
import { prettyLooseJson, URL_PATTERN, urlAtOffset } from '../format';

// Cmd (mac) / Ctrl + click on a URL inside the JSON opens it in a new tab —
// the readonly editor has no other use for a modified click.
//
// JSON 里对着链接 Cmd(mac)/Ctrl + 点击直接在新标签打开；
// 只读编辑器本来也不需要响应带修饰键的点击。
// Mark every URL so holding cmd/ctrl can underline it and show a pointer —
// without the hint the modified click is invisible.
//
// 给每个链接打标记，按住 cmd/ctrl 时加下划线和手型光标；
// 没有这个提示，用户根本看不出哪里能点。
const urlMatcher = new MatchDecorator({
  regexp: new RegExp(URL_PATTERN.source, 'g'),
  decoration: Decoration.mark({ class: 'cm-url' }),
});

const urlHighlight = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet;

    constructor(view: EditorView) {
      this.decorations = urlMatcher.createDeco(view);
    }

    update(update: ViewUpdate) {
      this.decorations = urlMatcher.updateDeco(update, this.decorations);
    }
  },
  { decorations: (plugin) => plugin.decorations },
);

const openUrlOnModClick = EditorView.domEventHandlers({
  mousedown(event, view) {
    if (!event.metaKey && !event.ctrlKey) return false;
    const pos = view.posAtCoords({ x: event.clientX, y: event.clientY });
    if (pos == null) return false;
    const line = view.state.doc.lineAt(pos);
    const url = urlAtOffset(line.text, pos - line.from);
    if (!url) return false;
    event.preventDefault();
    window.open(url, '_blank', 'noopener,noreferrer');
    return true;
  },
});

type JsonPaneProps = {
  raw: string;
};

export function JsonPane({ raw }: JsonPaneProps) {
  const parsed = useMemo(() => prettyLooseJson(raw), [raw]);
  const [formatted, setFormatted] = useState(true);
  const [copied, setCopied] = useState(false);
  const [modHeld, setModHeld] = useState(false);

  useEffect(() => {
    setFormatted(true);
  }, [raw]);

  useEffect(() => {
    const sync = (event: KeyboardEvent) => setModHeld(event.metaKey || event.ctrlKey);
    const clear = () => setModHeld(false);
    window.addEventListener('keydown', sync);
    window.addEventListener('keyup', sync);
    window.addEventListener('blur', clear);
    return () => {
      window.removeEventListener('keydown', sync);
      window.removeEventListener('keyup', sync);
      window.removeEventListener('blur', clear);
    };
  }, []);

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
    <div className={modHeld ? 'json-pane mod-held' : 'json-pane'}>
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
          extensions={
            prefix
              ? [urlHighlight, openUrlOnModClick]
              : [json(), urlHighlight, openUrlOnModClick]
          }
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
            matchesURL
            urlRegExp={/^https?:\/\/\S+$/}
          />
        </>
      )}
    </div>
  );
}
