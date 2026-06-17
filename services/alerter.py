import os
import httpx
import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / '.env')

ALERT_WEBHOOK_URL = os.getenv('ALERT_WEBHOOK_URL', '')
ALERT_LEVEL = os.getenv('ALERT_LEVEL', 'error')

LEVEL_ORDER = {'debug': 0, 'info': 1, 'warning': 2, 'error': 3, 'critical': 4}


async def send_alert(message: str, level: str = 'error'):
    if not ALERT_WEBHOOK_URL:
        return

    if LEVEL_ORDER.get(level, 3) < LEVEL_ORDER.get(ALERT_LEVEL, 3):
        return

    colors = {'info': 3066993, 'warning': 16776960, 'error': 15158332, 'critical': 10038562}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(ALERT_WEBHOOK_URL, json={
                'embeds': [{
                    'title': f'{level.upper()}',
                    'description': message[:2000],
                    'color': colors.get(level, 15158332),
                    'timestamp': __import__('datetime').datetime.utcnow().isoformat(),
                }],
            })
    except Exception:
        pass


def alert_sync(message: str, level: str = 'error'):
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(send_alert(message, level))
    except RuntimeError:
        asyncio.run(send_alert(message, level))
