"""Handles logging to a specific channel."""

import discord
import json
from pathlib import Path
from typing import List, Dict, Any

class LogHandler:
    def __init__(self, bot):
        self.bot = bot
        self.log_channels_file = Path(__file__).parent.parent / "config" / "log_channels.json"

    def _load_log_channels(self) -> List[int]:
        if not self.log_channels_file.exists():
            return []
        with open(self.log_channels_file, "r") as f:
            return json.load(f)

    async def log(self, embed: discord.Embed = None, file: discord.File = None):
        """Sends a log message to all log channels."""
        log_channels = self._load_log_channels()
        for channel_id in log_channels:
            try:
                channel = await self.bot.client.fetch_channel(channel_id)
                await channel.send(embed=embed, file=file)
            except (discord.NotFound, discord.Forbidden):
                pass

    async def log_command(self, message: discord.Message):
        """Logs a command usage."""
        embed = discord.Embed(
            title="Command Executed",
            description=f"**User:** {message.author.mention} (`{message.author.id}`)\n**Command:** `{message.content}`",
            color=discord.Color.blue(),
        )
        await self.log(embed=embed)

    async def log_error(self, error: str):
        """Logs an error."""
        embed = discord.Embed(
            title="Error Occurred",
            description=f"```\n{error}\n```",
            color=discord.Color.red(),
        )
        await self.log(embed=embed)

    async def log_api_request(self, request: Dict[str, Any], response: Dict[str, Any]):
        """Logs an API request and response."""
        request_file = discord.File(io.StringIO(json.dumps(request, indent=2)), filename="request.json")
        response_file = discord.File(io.StringIO(json.dumps(response, indent=2)), filename="response.json")
        embed = discord.Embed(
            title="API Request",
            color=discord.Color.green(),
        )
        await self.log(embed=embed, file=request_file)
        await self.log(embed=embed, file=response_file)
