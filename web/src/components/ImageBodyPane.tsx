import { useEffect, useState } from 'react';

type ImageBodyPaneProps = {
  url: string;
};

export function ImageBodyPane({ url }: ImageBodyPaneProps) {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [url]);

  if (!url) {
    return <p className="muted">empty</p>;
  }

  if (failed) {
    return (
      <div className="image-body-pane">
        <p className="muted">无法加载图片预览</p>
        <a className="mono image-preview-link" href={url} target="_blank" rel="noreferrer">
          {url}
        </a>
      </div>
    );
  }

  return (
    <div className="image-body-pane">
      <img
        className="image-preview"
        src={url}
        alt="Response image preview"
        loading="lazy"
        onError={() => setFailed(true)}
      />
      <a className="mono image-preview-link" href={url} target="_blank" rel="noreferrer">
        {url}
      </a>
    </div>
  );
}
