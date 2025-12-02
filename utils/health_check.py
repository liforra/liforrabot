"""Health check for the bot."""

import os
import asyncio
import hashlib
import psutil
import subprocess
from pathlib import Path
from time import monotonic

class HealthCheck:
    def __init__(self, bot, data_dir: Path):
        self.bot = bot
        self.data_dir = data_dir
        self.control_file = self.data_dir / "control.txt"
        self.last_hash = None
        self.pid = os.getpid()
        self.last_mount_check = 0.0
        self.remount_backoff = 30.0
        self.max_backoff = 600.0
        self.remount_failures = 0
        self.recovery_command = getattr(self.bot.config, "remount_command", "")
        self.recovery_method = getattr(self.bot.config, "remount_method", "rclone")

    async def run_checks(self):
        """Runs all health checks."""
        while True:
            await asyncio.sleep(60)  # Run checks every 60 seconds
            if not self.check_bot_crashed():
                await self.restart_bot("Bot crashed")
            if not self.check_can_read_files():
                await self.restart_bot("Cannot read files")
            if not await self.ensure_mount_access():
                await self.restart_bot("Mount inaccessible")
            if not self.check_control_file():
                await self.restart_bot("Control file changed")
            if not self.check_io_errors():
                await self.restart_bot("I/O error detected")

    def check_io_errors(self) -> bool:
        """Checks for I/O errors on the data directory."""
        try:
            # Attempt to read a file that should be accessible
            with open(self.bot.notes_file, "r") as f:
                pass
            return True
        except OSError as e:
            if "Transport endpoint is not connected" in str(e) or "Input/output error" in str(e):
                print(f"!!! I/O error detected: {e} !!!")
                return False
            return True # Ignore other OSErrors
        except Exception:
            return True # Ignore other exceptions

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

    async def ensure_mount_access(self) -> bool:
        """Attempts to verify and restore access to the data directory."""
        if await asyncio.to_thread(self._test_mount_access):
            self._reset_remount_state()
            return True

        wait_since_last = monotonic() - self.last_mount_check
        if wait_since_last < self.remount_backoff:
            return False

        self.last_mount_check = monotonic()
        self.remount_failures += 1
        self.remount_backoff = min(self.remount_backoff * 2, self.max_backoff)

        recovery_ok = await self._attempt_recovery()
        if recovery_ok and await asyncio.to_thread(self._test_mount_access):
            self._reset_remount_state()
            return True

        return False

    def _test_mount_access(self) -> bool:
        try:
            sentinel = self.data_dir / ".mount_probe"
            sentinel.write_text("ok", encoding="utf-8")
            sentinel.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def _reset_remount_state(self):
        self.remount_failures = 0
        self.remount_backoff = 30.0
        self.last_mount_check = monotonic()

    async def _attempt_recovery(self) -> bool:
        if not self.recovery_command:
            await self.bot.log_handler.log_error("Mount access lost; no recovery command configured")
            return False

        try:
            proc = await asyncio.create_subprocess_shell(
                self.recovery_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                await self.bot.log_handler.log_error(
                    f"Mount recovery succeeded using '{self.recovery_method}'"
                )
                return True
            await self.bot.log_handler.log_error(
                f"Mount recovery failed (code {proc.returncode}). stderr: {stderr.decode(errors='ignore')[:500]}"
            )
        except Exception as e:
            await self.bot.log_handler.log_error(f"Mount recovery invocation failed: {type(e).__name__}: {e}")
        return False

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
        sys.exit(1)
