interface TaskFiltersProps {
  status: string;
  urgency: string;
  onStatusChange: (s: string) => void;
  onUrgencyChange: (u: string) => void;
}

export default function TaskFilters({ status, urgency, onStatusChange, onUrgencyChange }: TaskFiltersProps) {
  return (
    <div className="flex gap-3 mb-4">
      <select
        value={status}
        onChange={e => onStatusChange(e.target.value)}
        className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm"
      >
        <option value="">All statuses</option>
        <option value="open">Open</option>
        <option value="in_progress">In Progress</option>
        <option value="done">Done</option>
      </select>
      <select
        value={urgency}
        onChange={e => onUrgencyChange(e.target.value)}
        className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm"
      >
        <option value="">All urgency</option>
        <option value="high">High</option>
        <option value="medium">Medium</option>
        <option value="low">Low</option>
      </select>
    </div>
  );
}
