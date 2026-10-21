"""Call summarization: parsing of LLM output and the Ollama HTTP contract."""
import json

import httpx
import pytest

from services.nlp_engine import summarizer
from services.nlp_engine.summarizer import _parse_response, summarize_call

TASKS = [{'content': 'Подготовить релиз', 'urgency': 'high'}]


class TestParseResponse:
    @pytest.mark.parametrize('text', [
        json.dumps(TASKS),
        '```json\n' + json.dumps(TASKS) + '\n```',
        '```\n' + json.dumps(TASKS) + '\n```',
        'Вот задачи из звонка: ' + json.dumps(TASKS) + ' Надеюсь, помог!',
    ], ids=['plain', 'markdown-json', 'markdown-bare', 'surrounded-by-prose'])
    def test_recovers_task_list(self, text):
        assert _parse_response(text) == TASKS

    @pytest.mark.parametrize('text', [
        '[]',
        '',
        'Задач не найдено',
        '{"content": "объект вместо массива"}',
        '[{"content": "обрезанный ответ", "urgency": "hi',
        '```json\nне json\n```',
    ], ids=['empty-list', 'empty-string', 'prose', 'object', 'truncated', 'broken-codeblock'])
    def test_returns_empty_list_when_nothing_usable(self, text):
        assert _parse_response(text) == []

    # Regression: a non-greedy `\[...\]` regex stopped at the first "]" inside a string.
    @pytest.mark.parametrize('payload', [
        [{'content': 'Обновить [README] и changelog', 'urgency': 'low'}],
        [{'content': 'Разобрать логи', 'urgency': 'medium', 'tags': ['ops', 'logs']}],
    ], ids=['bracket-in-text', 'nested-array'])
    def test_brackets_inside_tasks(self, payload):
        assert _parse_response('Ответ: ' + json.dumps(payload, ensure_ascii=False)) == payload

    def test_skips_stray_list_before_answer(self):
        assert _parse_response('Пункт [1] из протокола: ' + json.dumps(TASKS)) == TASKS


def _mock_ollama(monkeypatch, handler):
    """Route summarizer's httpx.AsyncClient through an in-process transport."""
    real_client = httpx.AsyncClient
    calls = []

    def recording(request):
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(summarizer.httpx, 'AsyncClient',
                        lambda **kw: real_client(transport=httpx.MockTransport(recording), **kw))
    return calls


class TestSummarizeCall:
    async def test_sends_prompt_and_parses_response(self, monkeypatch):
        calls = _mock_ollama(monkeypatch, lambda r: httpx.Response(200, json={'response': json.dumps(TASKS)}))

        result = await summarize_call(['Иван: я подготовлю релиз к пятнице', 'Пётр: отлично'])

        assert result == TASKS
        assert len(calls) == 1
        assert calls[0].url.path == '/api/generate'
        body = json.loads(calls[0].content)
        assert body['stream'] is False
        assert body['model'] == summarizer.OLLAMA_MODEL
        assert 'Иван: я подготовлю релиз к пятнице\nПётр: отлично' in body['prompt']

    @pytest.mark.parametrize('chunks', [[], ['   '], ['коротко'], ['ровно19символовтекс']])
    async def test_short_transcript_skips_llm(self, monkeypatch, chunks):
        calls = _mock_ollama(monkeypatch, lambda r: httpx.Response(200, json={'response': '[]'}))
        assert await summarize_call(chunks) == []
        assert calls == []

    async def test_twenty_chars_reaches_llm(self, monkeypatch):
        calls = _mock_ollama(monkeypatch, lambda r: httpx.Response(200, json={'response': '[]'}))
        text = 'а' * 20
        await summarize_call([text])
        assert len(calls) == 1

    @pytest.mark.parametrize('response', [
        httpx.Response(500, text='model crashed'),
        httpx.Response(404, json={'error': 'model not found'}),
        httpx.Response(200, text='not json at all'),
    ], ids=['500', '404', 'non-json-body'])
    async def test_ollama_failure_returns_empty(self, monkeypatch, response):
        _mock_ollama(monkeypatch, lambda r: response)
        assert await summarize_call(['достаточно длинная транскрипция звонка']) == []

    async def test_connection_error_returns_empty(self, monkeypatch):
        def refuse(request):
            raise httpx.ConnectError('connection refused', request=request)
        _mock_ollama(monkeypatch, refuse)
        assert await summarize_call(['достаточно длинная транскрипция звонка']) == []
