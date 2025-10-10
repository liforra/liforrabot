"""Message and attachment logging handlers."""

import aiofiles
import httpx
from pathlib import Path
from datetime import datetime
from utils.helpers import sanitize_filename
import discord


class LoggingHandler:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.dm_log_dir = data_dir / "logs" / "dms"
        self.guild_log_dir = data_dir / "logs"

    async def log_dm(self, message: discord.Message):
        """Logs a DM message."""
        try:
            if isinstance(message.channel, discord.DMChannel):
                log_dir_name = f"{sanitize_filename(message.channel.recipient.name)}-{message.channel.recipient.id}"
            elif isinstance(message.channel, discord.GroupChannel):
                log_dir_name = f"group-{sanitize_filename(message.channel.name or message.channel.id)}-{message.channel.id}"
            else:
                return

            log_dir = self.dm_log_dir / log_dir_name
            log_dir.mkdir(parents=True, exist_ok=True)

            content = message.content.replace("\n", "\\n")
            if message.attachments:
                content += f" [Attachments: {', '.join([att.filename for att in message.attachments])}]"

            log_entry = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {sanitize_filename(message.author.name)} ({message.author.id}): {content}\n"

            async with aiofiles.open(
                log_dir / "dm_log.txt", "a", encoding="utf-8"
            ) as f:
                await f.write(log_entry)
        except Exception as e:
            print(f"[LoggingHandler] Error logging DM: {e}")

    async def log_guild_message(self, message: discord.Message, should_log: bool):
        """Logs a guild message if enabled."""
        if not should_log:
            return

        try:
            log_dir = (
                self.guild_log_dir
                / f"{message.guild.id}-{sanitize_filename(message.guild.name)}"
                / sanitize_filename(message.channel.name)
            )
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{datetime.now().strftime('%Y-%m-%d')}.txt"

            content = message.content.replace("\n", "\\n")
            if message.attachments:
                content += f" [Attachments: {', '.join([att.filename for att in message.attachments])}]"

            log_entry = f"[{datetime.now().strftime('%H-%M-%S')}] {sanitize_filename(message.author.name)} ({message.author.id}): {content}\n"

            async with aiofiles.open(log_path, "a", encoding="utf-8") as f:
                await f.write(log_entry)
        except Exception as e:
            print(f"[LoggingHandler] Error logging guild message: {e}")

    async def log_guild_attachments(self, message: discord.Message, should_log: bool):
        """Logs guild message attachments if enabled."""
        if not message.attachments or not should_log:
            return

        try:
            attachments_dir = (
                self.guild_log_dir
                / f"{message.guild.id}-{sanitize_filename(message.guild.name)}"
                / sanitize_filename(message.channel.name)
                / "attachments"
            )
            attachments_dir.mkdir(parents=True, exist_ok=True)

            async with httpx.AsyncClient() as client:
                for attachment in message.attachments:
                    try:
                        filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}-{sanitize_filename(message.author.name)}-{sanitize_filename(attachment.filename)}"
                        response = await client.get(attachment.url, timeout=60)
                        response.raise_for_status()
                        async with aiofiles.open(attachments_dir / filename, "wb") as f:
                            await f.write(response.content)
                    except Exception as e:
                        print(
                            f"[LoggingHandler] Error downloading attachment {attachment.filename}: {e}"
                        )
        except Exception as e:
            print(f"[LoggingHandler] Error logging attachment: {e}")
