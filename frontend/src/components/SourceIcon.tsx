interface SourceIconProps {
  source: string | null;
}

const icons: Record<string, string> = {
  bot_server: '💬',
  oauth2_dm: '✉️',
  bot_voice: '🎙️',
  desktop_loopback: '🖥️',
};

export default function SourceIcon({ source }: SourceIconProps) {
  return <span title={source || 'unknown'}>{icons[source || ''] || '❓'}</span>;
}
