"""Health check for the bot."""

import os
import asyncio
import hashlib
import psutil
import subprocess
from pathlib import Path

class HealthCheck:
    def __init__(self, bot, data_dir: Path):
        self.bot = bot
        self.data_dir = data_dir
        self.control_file = self.data_dir / "control.txt"
        self.last_hash = None
        self.pid = os.getpid()

    async def run_checks(self):
        """Runs all health checks."""
        while True:
            await asyncio.sleep(60)  # Run checks every 60 seconds
            if not self.check_bot_crashed():
                await self.restart_bot("Bot crashed")
            if not self.check_can_read_files():
                await self.restart_bot("Cannot read files")
            if not self.check_mount_remounted():
                await self.restart_bot("Mount remounted")
            if not self.check_control_file():
                await self.restart_bot("Control file changed")

    def check_bot_crashed(self) -> bool:
        """Checks if one of the bots has crashed."""
        return psutil.pid_exists(self.pid)

    def check_can_read_files(self) -> bool:
        """Checks if the bot can read its files."""
        try:
            with open(self.bot.notes_file, "r") as f:
                pass
            return True
        except Exception:
            return False

    def check_mount_remounted(self) -> bool:
        """Checks if the mount where the bot lives has been remounted."""
        try:
            mount_output = subprocess.check_output(["mount"], text=True)
            for line in mount_output.splitlines():
                if str(self.data_dir) in line and "ro," in line.split(" ")[-2]:
                    return False
            return True
        except Exception:
            return True # Assume it's fine if we can't check

    def check_control_file(self) -> bool:
        """Checks if the control file has changed."""
        if not self.control_file.exists():
            self.generate_control_file()
            return True

        with open(self.control_file, "r") as f:
            current_hash = hashlib.sha256(f.read().encode()).hexdigest()

        if self.last_hash is None:
            self.last_hash = current_hash
            return True

        if self.last_hash != current_hash:
            self.last_hash = current_hash
            return False

        return True

    def generate_control_file(self):
        """Generates a new control file."""
        with open(self.control_file, "w") as f:
            f.write(os.urandom(32).hex())
        with open(self.control_file, "r") as f:
            self.last_hash = hashlib.sha256(f.read().encode()).hexdigest()

    async def restart_bot(self, reason: str):
        """Restarts the bot."""
        await self.bot.log_handler.log_error(f"Restarting bot: {reason}")
        await self.bot.client.close()
