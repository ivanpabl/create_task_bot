import asyncio
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from sqlalchemy import select

from services.database import get_async_session_factory, User, Task, SourceType
from services.nlp_engine import extract_tasks
from services.alerter import send_alert

load_dotenv(Path(__file__).resolve().parent.parent / '.env')

POLL_INTERVAL_SECONDS = 10
DISCORD_BOT_TOKEN = os.getenv('DISCORD_TOKEN')


async def poll_dms():
    factory = get_async_session_factory()
    if factory is None:
        print("[poller] No PostgreSQL configured, skipping")
        return

    if not DISCORD_BOT_TOKEN:
        print("[poller] No DISCORD_TOKEN, skipping")
        return

    bot_headers = {'Authorization': f'Bot {DISCORD_BOT_TOKEN}'}

    async with httpx.AsyncClient() as http:
        while True:
            try:
                # Get all DM channels for the bot
                channels_resp = await http.get(
                    'https://discord.com/api/users/@me/channels',
                    headers=bot_headers,
                )
                if channels_resp.status_code != 200:
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                    continue

                channels = channels_resp.json()
                if not isinstance(channels, list):
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                    continue

                async with factory() as db:
                    for channel in channels:
                        if channel.get('type') != 1:  # 1 = DM
                            continue

                        recipients = channel.get('recipients', [])
                        if not recipients:
                            continue

                        dm_user = recipients[0]
                        discord_id = dm_user.get('id')

                        # Find user in our DB by Discord ID
                        result = await db.execute(
                            select(User).where(User.discord_id == discord_id)
                        )
                        user = result.scalar_one_or_none()
                        if not user:
                            continue

                        # Fetch recent messages from this DM channel
                        msg_resp = await http.get(
                            f'https://discord.com/api/channels/{channel["id"]}/messages',
                            headers=bot_headers,
                            params={'limit': 10},
                        )
                        if msg_resp.status_code != 200:
                            continue

                        messages_data = msg_resp.json()
                        if not isinstance(messages_data, list):
                            continue

                        messages = [
                            {
                                'content': m.get('content', ''),
                                'author': m.get('author', {}).get('username', ''),
                                'author_id': m.get('author', {}).get('id', ''),
                                'channel': 'dm',
                                'timestamp': m.get('timestamp', ''),
                            }
                            for m in messages_data
                            if m.get('content')
                            and not m.get('author', {}).get('bot', False)
                        ]

                        if not messages:
                            continue

                        tasks = extract_tasks(messages)
                        for t in tasks:
                            task = Task(
                                user_id=user.id,
                                source=SourceType.oauth2_dm.value,
                                content=t['content'],
                                author=t['author'],
                                urgency=t['urgency'],
                                keywords=t['keywords'],
                                channel='dm',
                            )
                            db.add(task)

                        if tasks:
                            await db.commit()
                            print(f"[poller] Created {len(tasks)} task(s) from DM with {user.username}")

            except Exception as e:
                print(f"[poller] Error: {e}")
                await send_alert(f'Poller error: {e}')

            await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def start_poller():
    factory = get_async_session_factory()
    if factory is None:
        print("[poller] No PostgreSQL configured, DM polling disabled")
        return
    if not DISCORD_BOT_TOKEN:
        print("[poller] No DISCORD_TOKEN, DM polling disabled")
        return
    try:
        async with factory() as test_session:
            from sqlalchemy import text
            await test_session.execute(text('SELECT 1'))
    except Exception as e:
        print(f"[poller] PostgreSQL not available ({e}), DM polling disabled")
        return
    asyncio.create_task(poll_dms())
