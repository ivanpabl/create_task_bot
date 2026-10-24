export const URGENCY_META: Record<string, { label: string; badge: string }> = {
  high: { label: 'Высокая', badge: 'badge-red' },
  medium: { label: 'Средняя', badge: 'badge-amber' },
  low: { label: 'Низкая', badge: 'badge-green' },
};

export const STATUS_META: Record<string, { label: string; badge: string }> = {
  open: { label: 'Открыта', badge: '' },
  in_progress: { label: 'В работе', badge: 'badge-blue' },
  done: { label: 'Готово', badge: 'badge-green' },
};
