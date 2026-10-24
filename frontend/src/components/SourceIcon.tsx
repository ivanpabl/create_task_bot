import Icon, { type IconName } from './Icon';

const sources: Record<string, { icon: IconName; label: string }> = {
  bot_server: { icon: 'message', label: 'Сообщение на сервере' },
  oauth2_dm: { icon: 'mail', label: 'Личное сообщение' },
  bot_voice: { icon: 'mic', label: 'Голосовой канал' },
  desktop_loopback: { icon: 'monitor', label: 'Desktop-агент' },
};

function sourceLabel(source: string | null): string {
  return sources[source || '']?.label || source || 'Неизвестный источник';
}

export default function SourceIcon({ source }: { source: string | null }) {
  const icon = sources[source || '']?.icon || 'help';
  return (
    <span className="task-source" title={sourceLabel(source)}>
      <Icon name={icon} />
    </span>
  );
}
