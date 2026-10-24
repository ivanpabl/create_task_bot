import { useEffect, useState } from 'react';
import Icon from './Icon';

export default function CopyButton({ value, className = 'btn btn-secondary btn-sm' }: { value: string | null; className?: string }) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const t = window.setTimeout(() => setCopied(false), 1800);
    return () => window.clearTimeout(t);
  }, [copied]);

  const copy = async () => {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
    } catch {
      // Clipboard API is unavailable on plain http; the value stays visible for manual copy.
    }
  };

  return (
    <button className={className} onClick={copy} disabled={!value}>
      <Icon name={copied ? 'check' : 'copy'} className="icon icon-sm" />
      {copied ? 'Скопировано' : 'Копировать'}
    </button>
  );
}
