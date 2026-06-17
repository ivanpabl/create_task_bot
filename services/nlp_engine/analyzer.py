import re
from collections import defaultdict
from datetime import datetime

TASK_KEYWORDS = [
    'нужно', 'надо', 'необходимо', 'сделать', 'сделай', 'сделайте',
    'задача', 'задание', 'todo', 'to do', 'назначить', 'поручение',
    'выполнить', 'срок', 'дедлайн', 'deadline', 'запланировать',
    'запланируй', 'проверить', 'поправить', 'исправить', 'реализовать',
    'создать', 'подготовить', 'отправить', 'сдать', 'закончить',
    'завершить', 'пофиксить', 'починить', 'доделать', 'доделай',
    'займись', 'займитесь', 'смотрю', 'окей, я', 'ладно, я',
    'беру', 'возьму', 'берешь', 'возьмешь',
]

URGENCY_HIGH = ['срочно', 'важно', 'критично', 'asap', 'немедленно', 'горит']
URGENCY_MEDIUM = ['сегодня', 'завтра', 'на этой неделе', 'до вечера', 'до конца']

TASK_PATTERNS = [
    r'(нужно|надо|необходимо)\s+\w+',
    r'(сделать|сделай|сделайте)\s+\w+',
    r'кто\s+(сделает|займется|возьмет|может)',
    r'кто-нибудь\s+(сделает|займется|может)',
    r'(возьму|занимаюсь|делаю|сделаю)\s+',
    r'задача\s+(на|по|для)',
    r'(\?|вопрос)\s*(по|насчет|на счёт)',
    r'что\s+(думаешь|скажешь|предлагаешь)',
]


def extract_tasks(messages: list[dict]) -> list[dict]:
    tasks = []
    seen = set()

    for msg in messages:
        content = msg.get('content', '')
        if not content or len(content.strip()) < 5:
            continue

        content_lower = content.lower()

        found_keywords = [kw for kw in TASK_KEYWORDS if kw in content_lower]

        found_patterns = []
        for pattern in TASK_PATTERNS:
            if re.search(pattern, content_lower):
                found_patterns.append(pattern)

        if not found_keywords and not found_patterns:
            continue

        content_short = content[:100].lower()
        if content_short in seen:
            continue
        seen.add(content_short)

        urgency = 'low'
        if any(w in content_lower for w in URGENCY_HIGH):
            urgency = 'high'
        elif any(w in content_lower for w in URGENCY_MEDIUM):
            urgency = 'medium'

        assigned = re.findall(r'<@!?(\d+)>', content)

        tasks.append({
            'content': content,
            'author': msg.get('author', ''),
            'author_id': msg.get('author_id', ''),
            'channel': msg.get('channel', ''),
            'timestamp': msg.get('timestamp', ''),
            'urgency': urgency,
            'keywords': found_keywords,
            'assigned': assigned,
        })

    urgency_order = {'high': 0, 'medium': 1, 'low': 2}
    tasks.sort(key=lambda t: urgency_order.get(t['urgency'], 3))

    return tasks


def generate_summary(messages: list[dict]) -> str:
    if not messages:
        return "**Ежедневный отчёт**\n\nЗа последние 24 часа сообщений не было."

    by_channel = defaultdict(list)
    authors = set()

    for msg in messages:
        by_channel[msg.get('channel', 'unknown')].append(msg)
        authors.add(msg.get('author', ''))

    total = len(messages)

    lines = []
    lines.append("**Ежедневный отчёт**")
    lines.append(f"{datetime.now().strftime('%d.%m.%Y')}")
    lines.append("")
    lines.append(f"• Всего сообщений: **{total}**")
    lines.append(f"• Активных каналов: **{len(by_channel)}**")
    lines.append(f"• Участников: **{len(authors)}**")
    lines.append("")

    for channel, msgs in sorted(by_channel.items(), key=lambda x: len(x[1]), reverse=True):
        lines.append(f"**#{channel}** — {len(msgs)} сообщений:")
        sorted_msgs = sorted(msgs, key=lambda m: len(m.get('content', '')), reverse=True)
        for msg in sorted_msgs[:3]:
            content = msg.get('content', '')[:200]
            author = msg.get('author', 'unknown')
            lines.append(f"> {author}: {content}")
        lines.append("")

    tasks = extract_tasks(messages)
    if tasks:
        lines.append(f"**Актуальные задачи ({len(tasks)}):**")
        for t in tasks[:10]:
            marker = '[HIGH]' if t['urgency'] == 'high' else '[MED]' if t['urgency'] == 'medium' else '[LOW]'
            lines.append(f"{marker} **{t['author']}** в #{t['channel']}: {t['content'][:150]}")

    return '\n'.join(lines)
