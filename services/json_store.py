import json
from pathlib import Path
from datetime import datetime

STORE_PATH = Path(__file__).resolve().parents[1] / 'tasks_store.json'


def load_tasks() -> list[dict]:
    if not STORE_PATH.exists():
        return []
    try:
        with open(STORE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_tasks(tasks: list[dict]):
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STORE_PATH, 'w', encoding='utf-8') as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)


def add_task(
    user_id: str,
    content: str,
    urgency: str = 'low',
    keywords: list[str] = None,
    source: str = 'desktop_loopback',
    author: str = 'desktop',
) -> dict:
    tasks = load_tasks()
    task = {
        'id': str(len(tasks) + 1),
        'user_id': user_id,
        'content': content,
        'urgency': urgency,
        'keywords': keywords or [],
        'source': source,
        'author': author,
        'status': 'open',
        'dm_sent': 'pending',
        'created_at': datetime.utcnow().isoformat(),
    }
    tasks.append(task)
    save_tasks(tasks)
    return task


def get_pending_tasks() -> list[dict]:
    return [t for t in load_tasks() if t.get('dm_sent') == 'pending']


def mark_task_sent(task_id: str):
    tasks = load_tasks()
    for t in tasks:
        if t['id'] == task_id:
            t['dm_sent'] = 'sent'
            break
    save_tasks(tasks)
