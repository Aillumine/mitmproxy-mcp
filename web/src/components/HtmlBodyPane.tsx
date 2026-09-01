import { useEffect, useState } from 'react';
import { JsonPane } from './JsonPane';

type HtmlBodyPaneProps = {
  raw: string;
};

export function HtmlBodyPane({ raw }: HtmlBodyPaneProps) {
  const [view, setView] = useState<'source' | 'html'>('source');

  useEffect(() => {
    setView('source');
  }, [raw]);

  return (
    <div className="html-body-pane">
      <div className="body-view-toggle" role="tablist" aria-label="Body view">
        <button
          type="button"
          role="tab"
          aria-selected={view === 'source'}
          className={view === 'source' ? 'tab active' : 'tab'}
          onClick={() => setView('source')}
        >
          原文
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === 'html'}
          className={view === 'html' ? 'tab active' : 'tab'}
          onClick={() => setView('html')}
        >
          HTML
        </button>
      </div>
      {view === 'source' ? (
        <JsonPane raw={raw} />
      ) : (
        <iframe
          className="html-preview"
          title="Rendered HTML"
          sandbox=""
          srcDoc={raw}
        />
      )}
    </div>
  );
}
