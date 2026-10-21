"""extract_tasks: rule-based task detection in Discord messages.

Equivalence classes: keyword match / regex-pattern match / no match.
Boundaries: minimum content length (5 chars after strip), dedup prefix (100 chars).
"""
import pytest

from services.nlp_engine.analyzer import extract_tasks, generate_summary


def msg(content, **extra):
    return {'content': content, 'author': 'ivan', 'author_id': '1', 'channel': 'general',
            'timestamp': '2026-01-01T10:00:00', **extra}


class TestDetection:
    @pytest.mark.parametrize('text', [
        'Нужно обновить документацию',
        'надо проверить релиз',
        'Сделай ревью пулл-реквеста',
        'Дедлайн по отчёту в пятницу',
        'TODO: убрать хардкод',
        'Возьму эту задачу на себя',
        'Поправить вёрстку на мобилке',
    ], ids=['нужно', 'надо', 'сделай', 'дедлайн', 'todo', 'возьму', 'поправить'])
    def test_keyword_makes_task(self, text):
        tasks = extract_tasks([msg(text)])
        assert len(tasks) == 1
        assert tasks[0]['content'] == text

    @pytest.mark.parametrize('text', [
        'Кто займется миграцией?',
        'кто-нибудь может глянуть логи',
        'Что думаешь про новый дизайн',
    ])
    def test_pattern_without_keyword_makes_task(self, text):
        assert len(extract_tasks([msg(text)])) == 1

    @pytest.mark.parametrize('text', [
        'Привет всем, как дела?',
        'Отличная погода сегодня',
        'ахаха смешно',
    ])
    def test_plain_chat_is_ignored(self, text):
        assert extract_tasks([msg(text)]) == []

    def test_keyword_match_is_case_insensitive(self):
        tasks = extract_tasks([msg('НУЖНО СРОЧНО ПОЧИНИТЬ ПРОД')])
        assert tasks and 'нужно' in tasks[0]['keywords']

    def test_found_keywords_are_reported(self):
        tasks = extract_tasks([msg('Нужно исправить баг до дедлайна')])
        assert {'нужно', 'исправить', 'дедлайн'} <= set(tasks[0]['keywords'])


class TestMinimumLength:
    # 'надо' is a keyword: only the length rule decides the outcome.
    @pytest.mark.parametrize('text, expected', [
        ('', 0),
        ('    ', 0),
        ('надо', 0),         # 4 chars: below the boundary
        ('надо!', 1),        # 5 chars: on the boundary
        ('  надо  ', 0),     # whitespace does not count
        ('надо!!', 1),       # above the boundary
    ])
    def test_length_boundary(self, text, expected):
        assert len(extract_tasks([msg(text)])) == expected

    def test_message_without_content_key(self):
        assert extract_tasks([{'author': 'ivan'}]) == []


class TestUrgency:
    @pytest.mark.parametrize('text, urgency', [
        ('Срочно нужно поднять сервер', 'high'),
        ('нужно сделать ASAP', 'high'),
        ('Горит! надо выкатить фикс', 'high'),
        ('Нужно сделать до вечера', 'medium'),
        ('надо отправить отчёт завтра', 'medium'),
        ('Нужно обновить зависимости', 'low'),
    ])
    def test_urgency_levels(self, text, urgency):
        assert extract_tasks([msg(text)])[0]['urgency'] == urgency

    def test_high_wins_over_medium(self):
        assert extract_tasks([msg('Срочно нужно сделать сегодня')])[0]['urgency'] == 'high'

    def test_sorted_by_urgency(self):
        tasks = extract_tasks([
            msg('Нужно обновить README'),
            msg('Нужно сделать отчёт завтра'),
            msg('Срочно нужно починить оплату'),
        ])
        assert [t['urgency'] for t in tasks] == ['high', 'medium', 'low']


class TestDeduplication:
    def test_same_text_counted_once(self):
        tasks = extract_tasks([msg('Нужно обновить README'), msg('Нужно обновить README')])
        assert len(tasks) == 1

    def test_dedup_is_case_insensitive(self):
        assert len(extract_tasks([msg('Нужно обновить README'), msg('нужно ОБНОВИТЬ readme')])) == 1

    def test_messages_differing_after_100_chars_are_merged(self):
        prefix = 'Нужно ' + 'x' * 94          # exactly 100 chars
        assert len(prefix) == 100
        tasks = extract_tasks([msg(prefix + ' первый'), msg(prefix + ' второй')])
        assert len(tasks) == 1

    def test_messages_differing_within_100_chars_are_kept(self):
        prefix = 'Нужно ' + 'x' * 93          # 99 chars, the 100th char differs
        tasks = extract_tasks([msg(prefix + 'A'), msg(prefix + 'B')])
        assert len(tasks) == 2


class TestFields:
    def test_mentions_are_extracted_as_assignees(self):
        tasks = extract_tasks([msg('<@123> и <@!456> нужно сделать деплой')])
        assert tasks[0]['assigned'] == ['123', '456']

    def test_source_fields_are_copied(self):
        task = extract_tasks([msg('Нужно сделать деплой', channel='backend')])[0]
        assert task['author'] == 'ivan'
        assert task['channel'] == 'backend'
        assert task['timestamp'] == '2026-01-01T10:00:00'

    def test_empty_input(self):
        assert extract_tasks([]) == []


class TestSummary:
    def test_no_messages(self):
        assert 'сообщений не было' in generate_summary([])

    def test_counts_and_tasks(self):
        report = generate_summary([
            msg('Срочно нужно починить оплату', author='a'),
            msg('Привет всем', author='b', channel='random'),
        ])
        assert 'Всего сообщений: **2**' in report
        assert 'Активных каналов: **2**' in report
        assert 'Участников: **2**' in report
        assert '[HIGH]' in report
