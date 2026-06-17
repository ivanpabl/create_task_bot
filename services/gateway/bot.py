import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import discord
import asyncio
import os
from dotenv import load_dotenv
from discord.ext import commands
from sqlalchemy import select

from services.database import init_db, get_async_session_factory, User, Task
from services.json_store import get_pending_tasks as json_get_pending, mark_task_sent as json_mark_sent, add_task as json_add_task
from services.nlp_engine import extract_tasks
from services.alerter import send_alert

load_dotenv(Path(__file__).resolve().parents[2] / '.env')

TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    raise ValueError("DISCORD_TOKEN not found in .env")

DM_POLL_INTERVAL = float(os.getenv('DM_POLL_INTERVAL', '5'))

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)


async def send_dm(user_id: str, content: str, urgency: str, source: str, keywords: list[str]):
    try:
        discord_user = bot.get_user(int(user_id))
        if discord_user is None:
            discord_user = await bot.fetch_user(int(user_id))

        urgency_emoji = {'high': '🔴', 'medium': '🟡', 'low': '🟢'}
        emoji = urgency_emoji.get(urgency, '⚪')

        embed = discord.Embed(
            title=f"{emoji} Новая задача",
            description=content[:2000],
            color=(
                discord.Color.red() if urgency == 'high'
                else discord.Color.yellow() if urgency == 'medium'
                else discord.Color.green()
            ),
        )
        embed.add_field(name='Источник', value=source or 'desktop', inline=True)
        if keywords:
            embed.add_field(name='Ключевые слова', value=', '.join(keywords), inline=False)

        await discord_user.send(embed=embed)
        return True
    except discord.Forbidden:
        print(f"[bot] Cannot DM user {user_id} — DMs closed")
        return False
    except Exception as e:
        print(f"[bot] DM error for {user_id}: {e}")
        return False


async def dm_delivery_loop():
    await bot.wait_until_ready()
    factory = get_async_session_factory()
    has_db = factory is not None

    if not has_db:
        print("[bot] No PostgreSQL — using JSON storage")

    print(f"[bot] DM delivery loop started (poll every {DM_POLL_INTERVAL}s)")

    while not bot.is_closed():
        try:
            if has_db:
                async with factory() as db:
                    result = await db.execute(
                        select(Task).where(
                            Task.dm_sent == 'pending',
                            Task.user_id.isnot(None),
                        ).limit(20)
                    )
                    pg_tasks = result.scalars().all()

                    for task in pg_tasks:
                        result = await db.execute(
                            select(User).where(User.id == task.user_id)
                        )
                        user = result.scalar_one_or_none()
                        if not user or not user.discord_id:
                            task.dm_sent = 'failed'
                            continue

                        ok = await send_dm(
                            user.discord_id, task.content,
                            task.urgency, task.source or '', task.keywords or []
                        )
                        task.dm_sent = 'sent' if ok else 'failed'
                        if ok:
                            print(f"[bot] DM sent to {user.username}")

                    await db.commit()
            else:
                json_tasks = json_get_pending()
                for task in json_tasks:
                    ok = await send_dm(
                        task.get('user_id', ''), task.get('content', ''),
                        task.get('urgency', 'low'), task.get('source', ''),
                        task.get('keywords', [])
                    )
                    if ok:
                        json_mark_sent(task['id'])
                        print(f"[bot] DM sent (JSON task {task['id']})")

        except Exception as e:
            print(f"[bot] DM loop error: {e}")
            await send_alert(f'DM loop error: {e}')

        await asyncio.sleep(DM_POLL_INTERVAL)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    extracted = extract_tasks([{
        'content': message.content,
        'author': message.author.name,
        'author_id': str(message.author.id),
        'channel': message.channel.name if hasattr(message.channel, 'name') else 'dm',
        'timestamp': message.created_at.isoformat(),
    }])

    if not extracted:
        return

    factory = get_async_session_factory()

    if factory is not None:
        async with factory() as db:
            result = await db.execute(
                select(User).where(User.discord_id == str(message.author.id))
            )
            user = result.scalar_one_or_none()

            if user:
                for t in extracted:
                    task = Task(
                        id=uuid4(),
                        user_id=user.id,
                        source='bot_server' if message.guild else 'oauth2_dm',
                        content=t['content'],
                        author=t['author'],
                        urgency=t['urgency'],
                        keywords=t['keywords'],
                        channel=message.channel.name if hasattr(message.channel, 'name') else 'dm',
                        guild_id=str(message.guild.id) if message.guild else None,
                        dm_sent='pending',
                    )
                    db.add(task)
                await db.commit()
                print(f"[bot] Created {len(extracted)} task(s) from {message.author}")
    else:
        for t in extracted:
            json_add_task(
                user_id=str(message.author.id),
                content=t['content'],
                urgency=t['urgency'],
                keywords=t['keywords'],
                source='bot_server' if message.guild else 'oauth2_dm',
                author=message.author.name,
            )
        print(f"[bot] Created {len(extracted)} task(s) from {message.author} (JSON)")


@bot.event
async def on_ready():
    await init_db()
    await send_alert(f'Бот {bot.user} подключился!', 'info')
    print(f'{bot.user} подключился!')
    bot.loop.create_task(dm_delivery_loop())


if __name__ == '__main__':
    bot.run(TOKEN)
