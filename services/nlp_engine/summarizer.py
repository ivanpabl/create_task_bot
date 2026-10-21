import json
import re

import httpx

from services.web.config import settings

OLLAMA_URL = settings.ollama_url.rstrip('/')
OLLAMA_MODEL = settings.ollama_model

SUMMARY_PROMPT = (
    "You are an assistant that extracts concrete tasks and action items from a work call transcript.\n"
    "Return ONLY a valid JSON array of objects. No explanation, no markdown.\n"
    "Each object: {\"content\": \"task description (1-2 sentences)\", \"urgency\": \"high\" | \"medium\" | \"low\"}\n"
    "Include only tasks someone agreed to do or that were decided upon.\n"
    "Ignore general discussion, questions, small talk.\n"
    "If no tasks found, return []\n\n"
    "Transcript:\n"
)


async def summarize_call(transcriptions: list[str]) -> list[dict]:
    if not transcriptions:
        return []

    aggregated = '\n'.join(transcriptions)
    if len(aggregated.strip()) < 20:
        return []

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=3.0)) as client:
            resp = await client.post(f'{OLLAMA_URL}/api/generate', json={
                'model': OLLAMA_MODEL,
                'prompt': SUMMARY_PROMPT + aggregated,
                'stream': False,
                'options': {
                    'temperature': 0.1,
                    'num_predict': 2000,
                },
            })
            resp.raise_for_status()
            data = resp.json()
            response_text = data.get('response', '').strip()
    except Exception as e:
        print(f"[summarizer] Ollama error: {e}")
        return []

    return _parse_response(response_text)


def _parse_response(text: str) -> list[dict]:
    # Try direct JSON parse
    try:
        tasks = json.loads(text)
        if isinstance(tasks, list):
            return tasks
    except json.JSONDecodeError:
        pass

    # Try extracting JSON from markdown code blocks
    match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if match:
        try:
            tasks = json.loads(match.group(1))
            if isinstance(tasks, list):
                return tasks
        except json.JSONDecodeError:
            pass

    # Try to find a JSON array anywhere in the text. A regex can't tell a closing
    # bracket from one inside a string, so let the decoder find where the array ends.
    decoder = json.JSONDecoder()
    for match in re.finditer(r'\[', text):
        try:
            tasks, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        # Skip stray lists like "пункт [1]" that precede the real answer.
        if isinstance(tasks, list) and all(isinstance(t, dict) for t in tasks):
            return tasks

    print(f"[summarizer] Could not parse response: {text[:300]}")
    return []
