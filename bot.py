"""Main Bot class with event handlers."""

import asyncio
import re
import json
import logging
from pathlib import Path
from typing import Dict, Optional, Union
from datetime import datetime, timedelta

from config.config_manager import ConfigManager
from handlers.alts_handler import AltsHandler
from handlers.ip_handler import IPHandler
from handlers.logging_handler import LoggingHandler
from commands.user_commands import UserCommands
from commands.admin_commands import AdminCommands
from utils.constants import SWEAR_WORDS, SLUR_WORDS, ASTEROIDE_BOT_ID
from utils.helpers import (
    split_message,
    calculate_edit_percentage,
    is_likely_typo,
)


class Bot:
    def __init__(self, token: str, data_dir: Path, token_type: str = "bot"):
        self.token = token
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.token_type = token_type

        # File paths
        self.notes_file = self.data_dir / "notes.json"
        self.log_file = self.data_dir / "bot.log"
        self.user_tokens_file = self.data_dir / "user-tokens.json"

        # Initialize Discord client based on token type
        if token_type == "bot":
            import discord
            from discord import app_commands
            self.discord = discord
            self.app_commands = app_commands
            intents = discord.Intents.default()
            intents.message_content = True
            intents.members = True
            intents.presences = True
            self.client = discord.Client(intents=intents)
            self.tree = app_commands.CommandTree(self.client)
        else:  # user/selfbot
            import selfcord as discord
            self.discord = discord
            self.app_commands = None
            self.client = discord.Client()
            self.tree = None

        # Initialize handlers
        self.config = ConfigManager(data_dir)
        self.alts_handler = None  # Will be initialized after config load
        self.ip_handler = IPHandler(data_dir)
        self.logging_handler = LoggingHandler(data_dir)

        # Initialize command handlers
        self.user_commands_handler = UserCommands(self)
        self.admin_commands_handler = AdminCommands(self)

        # Data storage
        self.notes_data = {"public": {}, "private": {}}
        self.forward_cache = {}
        self.message_cache = {}
        self.edit_history = {}

        # Command mappings
        self.user_commands = {
            "trump": self.user_commands_handler.command_trump,
            "websites": self.user_commands_handler.command_websites,
            "pings": self.user_commands_handler.command_pings,
            "note": self.user_commands_handler.command_note,
            "help": self.user_commands_handler.command_help,
            "ip": self.user_commands_handler.command_ip,
        }
        self.admin_commands = {
            "reload-config": self.admin_commands_handler.command_reload_config,
            "config": self.admin_commands_handler.command_config,
            "resend": self.admin_commands_handler.command_resend,
            "override": self.admin_commands_handler.command_override,
            "alts": self.admin_commands_handler.command_alts,
            "qrlogin": self.admin_commands_handler.command_qrlogin,
        }

        # Help texts
        self.command_help_texts = {
            "alts": "Usage: `{p}alts <username|list|stats|refresh|clean>`\n- `<username>`: Looks up a user's known alts and IPs.\n- `list [page]`: Shows a paginated list of all tracked users.\n- `stats`: Displays database statistics.\n- `refresh`: Manually fetches new data from the remote source.\n- `clean`: Removes empty/lone entries.\n- `clean --ip`: Moves usernames that are IPs into the IP list.\n- `clean --spigey`: (Admin) Special tool to fix impersonation data for 'Spigey'.",
            "config": "Usage: `{p}config <get|set|debug> [path] [value]`\n- `get <path>`: Retrieves a configuration value (e.g., `general.prefix`).\n- `set <path> <value>`: Sets a configuration value.\n- `debug`: Shows your current effective settings in this channel.",
            "qrlogin": "Usage: `{p}qrlogin [#channel] [message]`\nGenerates a temporary QR code for another user to log in and save their token. Use with extreme caution and explicit consent.",
            "override": "Usage: `{p}override <user|channel> <@mention|#mention> <setting> <value>`\nSets a setting override for a specific user or channel in the current guild.",
            "note": "Usage: `{p}note <create|get|list|delete> <public|private> [name] [content]`\nManages personal (private) or server-wide (public) notes.",
            "reload-config": "Usage: `{p}reload-config`\nReloads `config.toml`, `notes.json`, and `alts_data.json` from disk.",
            "resend": "Usage: `{p}resend <number>`\nDeletes the command and resends the last `X` messages sent by the bot in the current channel.",
            "ip": "Usage: `{p}ip <info|db> [args]`\n- `info <ip>`: Fetches live information about an IP address (supports IPv4 and IPv6).\n- `db info <ip>`: Shows cached IP information from the database.\n- `db list [page]`: Lists all IPs in the database.\n- `db search <term>`: Searches IPs by country, city, or ISP.\n- `db refresh`: Updates all cached IP information.",
        }

        # Register event handlers
        self.client.event(self.on_ready)
        self.client.event(self.on_message)
        self.client.event(self.on_message_edit)
        self.client.event(self.on_message_delete)
        self.client.event(self.on_presence_update)

        # Register slash commands if bot token
        if self.token_type == "bot":
            self.register_slash_commands()

    def register_slash_commands(self):
        """Registers slash commands for bot tokens."""
        if not self.tree:
            return

        # Trump command
        @self.tree.command(name="trump", description="Get a random Trump quote")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def trump_slash(interaction: self.discord.Interaction):
            await interaction.response.defer()
            
            # Create a mock message object
            class MockMessage:
                def __init__(self, interaction):
                    self.channel = interaction.channel
                    self.guild = interaction.guild
                    self.author = interaction.user
            
            mock_msg = MockMessage(interaction)
            
            # Call the text command
            import httpx
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        "https://api.whatdoestrumpthink.com/api/v1/quotes/random",
                        timeout=10,
                    )
                    response.raise_for_status()
                    quote = response.json().get("message", "Could not retrieve a quote.")
                    await interaction.followup.send(f'"{quote}" ~Donald Trump')
            except Exception as e:
                await interaction.followup.send(f"Sorry, an error occurred: {type(e).__name__}")

        # Websites command
        @self.tree.command(name="websites", description="Check status of configured websites")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def websites_slash(interaction: self.discord.Interaction):
            await interaction.response.defer()
            
            class MockMessage:
                def __init__(self, interaction):
                    self.channel = interaction.channel
                    self.guild = interaction.guild
                    self.author = interaction.user
            
            mock_msg = MockMessage(interaction)
            
            gid = interaction.guild.id if interaction.guild else None
            sites = self.config.get_guild_config(
                gid,
                "websites",
                self.config.default_websites,
                interaction.user.id,
                interaction.channel.id,
            )
            friend_sites = self.config.get_guild_config(
                gid,
                "friend_websites",
                self.config.default_friend_websites,
                interaction.user.id,
                interaction.channel.id,
            )
            
            main_res, friend_res = await asyncio.gather(
                self.user_commands_handler._check_and_format_sites(sites, "Websites"),
                self.user_commands_handler._check_and_format_sites(friend_sites, "Friends Websites"),
            )
            
            final_output = []
            if main_res:
                final_output.extend(main_res)
            if friend_res:
                if final_output:
                    final_output.append("")
                final_output.extend(friend_res)
            
            content = "\n".join(final_output) if final_output else "No websites are configured."
            await interaction.followup.send(content)

        # IP Info command
        @self.tree.command(name="ip", description="Get information about an IP address")
        @self.app_commands.describe(ip="The IP address to look up (IPv4 or IPv6)")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def ip_slash(interaction: self.discord.Interaction, ip: str):
            await interaction.response.defer()
            
            from utils.helpers import is_valid_ip, is_valid_ipv6
            from utils.constants import COUNTRY_FLAGS
            
            if not is_valid_ip(ip):
                await interaction.followup.send("❌ Invalid IP address format")
                return
            
            ip_data = await self.ip_handler.fetch_ip_info(ip)
            
            if not ip_data:
                await interaction.followup.send(f"❌ Failed to fetch info for `{ip}`")
                return
            
            flag = COUNTRY_FLAGS.get(ip_data.get("countryCode", ""), "🌐")
            
            if is_valid_ipv6(ip):
                ip_header = f"**IP Information for `{ip}`:**"
            else:
                ip_header = f"**IP Information for [{ip}](<https://whatismyipaddress.com/ip/{ip}>):**"
            
            output = [
                ip_header,
                f"{flag} **Country:** {ip_data.get('country', 'N/A')} ({ip_data.get('countryCode', 'N/A')})",
                f"**Region:** {ip_data.get('regionName', 'N/A')}",
                f"**City:** {ip_data.get('city', 'N/A')}",
                f"**ZIP:** {ip_data.get('zip', 'N/A')}",
                f"**Coordinates:** {ip_data.get('lat', 'N/A')}, {ip_data.get('lon', 'N/A')}",
                f"**Timezone:** {ip_data.get('timezone', 'N/A')}",
                f"**ISP:** {ip_data.get('isp', 'N/A')}",
                f"**Organization:** {ip_data.get('org', 'N/A')}",
                f"**AS:** {ip_data.get('as', 'N/A')}",
            ]
            
            vpn_provider = self.ip_handler.detect_vpn_provider(
                ip_data.get("isp", ""), ip_data.get("org", "")
            )
            
            if vpn_provider:
                output.append(f"**VPN Provider:** {vpn_provider}")
            elif ip_data.get("proxy"):
                output.append(f"**Proxy/VPN:** Yes")
            
            if ip_data.get("hosting"):
                output.append(f"**VPS/Hosting:** Yes")
            
            await interaction.followup.send("\n".join(output))

        # Alts command
        @self.tree.command(name="alts", description="Look up a user's known alts and IPs")
        @self.app_commands.describe(username="The username to look up")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def alts_slash(interaction: self.discord.Interaction, username: str):
            await interaction.response.defer()
            
            if not str(interaction.user.id) in self.config.admin_ids:
                await interaction.followup.send("❌ This command is admin-only.")
                return
            
            from utils.helpers import format_alt_name, format_alts_grid, is_valid_ipv4, is_valid_ipv6
            
            search_term = username
            found_user = None
            
            lowercase_map = {
                k.lower(): k for k in self.alts_handler.alts_data.keys()
            }
            
            if search_term.startswith("."):
                if search_term.lower() in lowercase_map:
                    found_user = lowercase_map[search_term.lower()]
            else:
                search_candidates = [
                    search_term,
                    f".{search_term}",
                    f"...{search_term}",
                ]
                for candidate in search_candidates:
                    if candidate.lower() in lowercase_map:
                        found_user = lowercase_map[candidate.lower()]
                        break
            
            if not found_user:
                await interaction.followup.send(f"❌ No data for `{search_term}`")
                return
            
            data = self.alts_handler.alts_data[found_user]
            alts = sorted(list(data.get("alts", set())))
            ips = sorted(list(data.get("ips", set())))
            
            formatted_found_user = format_alt_name(found_user)
            output = [f"**Alts data for {formatted_found_user}:**"]
            
            if alts:
                formatted_alts = [format_alt_name(alt) for alt in alts]
                grid_lines = format_alts_grid(formatted_alts, max_per_line=3)
                output.append(f"**Alts ({len(alts)}):**")
                output.extend(grid_lines)
            
            if ips:
                output.append(f"\n**IPs ({len(ips)}):**")
                for ip in ips:
                    output.append(f"→ {self.ip_handler.format_ip_with_geo(ip)}")
            
            output.append(
                f"\n*First seen: {data.get('first_seen', 'N/A')[:10]} | Last updated: {data.get('last_updated', 'N/A')[:10]}*"
            )
            
            await interaction.followup.send("\n".join(output))

        # Help command
        @self.tree.command(name="help", description="Show available commands")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def help_slash(interaction: self.discord.Interaction):
            p = self.config.get_prefix(interaction.guild.id if interaction.guild else None)
            
            help_text = (
                f"**Text Commands:** Use prefix `{p}` followed by:\n"
                f"`trump`, `websites`, `pings`, `note`, `ip`, `help`\n\n"
                f"**Slash Commands:** Available via `/`:\n"
                f"`/trump`, `/websites`, `/ip`, `/alts`, `/help`\n\n"
                f"*Type `{p}help <command>` for detailed text command info.*"
            )
            
            if str(interaction.user.id) in self.config.admin_ids:
                admin_cmds = ", ".join(f"`{cmd}`" for cmd in self.admin_commands.keys())
                help_text += f"\n\n**Admin Commands:** {admin_cmds}"
            
            await interaction.response.send_message(help_text)

    async def run(self):
        """Starts the bot."""
        print(f"Starting bot instance ({self.token_type}) in directory: {self.data_dir}")
        self.config.load_config()

        # Initialize alts handler after config is loaded
        self.alts_handler = AltsHandler(self.data_dir, self.config.default_clean_spigey)
        self.alts_handler.load_and_preprocess_alts_data()

        self.load_notes()

        handler = logging.FileHandler(
            filename=self.log_file, encoding="utf-8", mode="w"
        )
        
        # Handle logging setup differences between discord.py and selfcord
        try:
            self.discord.utils.setup_logging(handler=handler, root=False)
        except AttributeError:
            # selfcord might not have setup_logging
            logging.basicConfig(handlers=[handler], level=logging.INFO)

        try:
            await self.client.start(self.token)
        except Exception as e:
            if "LoginFailure" in str(type(e).__name__):
                print(f"!!! LOGIN FAILED for {self.token_type} in {self.data_dir}. Check token. !!!")
            else:
                print(f"An unexpected error occurred for {self.token_type} in {self.data_dir}: {e}")

    def load_notes(self):
        """Loads notes from disk."""
        if self.notes_file.exists():
            try:
                with open(self.notes_file, "r", encoding="utf-8") as f:
                    self.notes_data = json.load(f)
            except Exception as e:
                print(f"[{self.data_dir.name}] Error loading notes: {e}")
                self.notes_data = {"public": {}, "private": {}}
        else:
            self.notes_data = {"public": {}, "private": {}}

    def save_notes(self):
        """Saves notes to disk."""
        try:
            with open(self.notes_file, "w", encoding="utf-8") as f:
                json.dump(self.notes_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[{self.data_dir.name}] Error saving notes: {e}")

    def load_user_tokens(self) -> Dict:
        """Loads user tokens from disk."""
        if not self.user_tokens_file.exists():
            return {}
        try:
            with open(self.user_tokens_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def save_user_tokens(self, tokens: Dict):
        """Saves user tokens to disk."""
        try:
            with open(self.user_tokens_file, "w", encoding="utf-8") as f:
                json.dump(tokens, f, indent=4)
        except IOError as e:
            print(f"[Token Storage] Error saving user tokens: {e}")

    def censor_text(self, text: str, guild_id: Optional[int] = None) -> str:
        """Censors swear words and slurs based on config."""
        if not text or not isinstance(text, str):
            return text or ""

        allow_swears = self.config.get_guild_config(
            guild_id, "allow-swears", self.config.default_allow_swears
        )
        allow_slurs = self.config.get_guild_config(
            guild_id, "allow-slurs", self.config.default_allow_slurs
        )

        if not allow_slurs:
            for slur in SLUR_WORDS:
                text = re.compile(re.escape(slur), re.IGNORECASE).sub(
                    "█" * len(slur), text
                )

        if not allow_swears:
            for swear in SWEAR_WORDS:
                text = re.compile(re.escape(swear), re.IGNORECASE).sub(
                    "*" * len(swear), text
                )

        return text

    async def bot_send(self, channel, content=None, files=None):
        """Sends a message with censoring and splitting support."""
        censored_content = (
            self.censor_text(
                content,
                channel.guild.id
                if hasattr(channel, "guild") and channel.guild
                else None,
            )
            if content
            else ""
        )

        try:
            if not censored_content:
                if files:
                    return await channel.send(files=files, suppress_embeds=True)
                return None

            message_chunks = split_message(censored_content)

            first = True
            sent_message = None
            for chunk in message_chunks:
                if first:
                    sent_message = await channel.send(
                        content=chunk, files=files, suppress_embeds=True
                    )
                    first = False
                    files = None
                else:
                    await channel.send(content=chunk, suppress_embeds=True)

            return sent_message

        except Exception as e:
            error_name = type(e).__name__
            if "Forbidden" in error_name:
                print(f"[{self.client.user}] Missing permissions in channel {channel.id}")
            else:
                print(f"[{self.client.user}] Error sending message: {e}")
        return None

    async def cleanup_forward_cache(self):
        """Periodically cleans the forward cache."""
        await self.client.wait_until_ready()
        while not self.client.is_closed():
            await asyncio.sleep(3600)
            cutoff = datetime.now() - timedelta(hours=24)
            expired_ids = [
                msg_id
                for msg_id, data in self.forward_cache.items()
                if data["timestamp"] < cutoff
            ]
            if expired_ids:
                for msg_id in expired_ids:
                    del self.forward_cache[msg_id]
                print(
                    f"[{self.client.user}] Cleaned up {len(expired_ids)} old entries from forward cache."
                )

    async def cleanup_message_cache(self):
        """Periodically cleans the message cache."""
        await self.client.wait_until_ready()
        while not self.client.is_closed():
            await asyncio.sleep(600)
            cutoff = datetime.now() - timedelta(minutes=10)
            expired_ids = [
                msg_id
                for msg_id, data in self.message_cache.items()
                if data["timestamp"] < cutoff
            ]
            if expired_ids:
                for msg_id in expired_ids:
                    del self.message_cache[msg_id]
                print(
                    f"[{self.client.user}] Cleaned up {len(expired_ids)} old entries from the message cache."
                )

            edit_expired_ids = [
                msg_id
                for msg_id, data in self.edit_history.items()
                if datetime.now()
                - datetime.fromisoformat(
                    data.get("timestamp", datetime.now().isoformat())
                )
                > timedelta(minutes=10)
            ]
            if edit_expired_ids:
                for msg_id in edit_expired_ids:
                    del self.edit_history[msg_id]
                print(
                    f"[{self.client.user}] Cleaned up {len(edit_expired_ids)} old entries from edit history."
                )

    async def handle_command(
        self, message, command_name: str, args: list
    ):
        """Routes commands to appropriate handlers."""
        try:
            if command_name in self.user_commands:
                await self.user_commands[command_name](message, args)
            elif (
                command_name in self.admin_commands
                and str(message.author.id) in self.config.admin_ids
            ):
                await self.admin_commands[command_name](message, args)
        except Exception as e:
            print(f"[{self.client.user}] Error executing command '{command_name}': {e}")
            import traceback

            traceback.print_exc()

    async def on_ready(self):
        """Called when the bot is ready."""
        print(f"Logged in as {self.client.user} (ID: {self.client.user.id}) [Type: {self.token_type}]")

        # Sync slash commands for bots
        if self.token_type == "bot" and self.tree:
            try:
                print(f"[{self.client.user}] Syncing slash commands...")
                synced = await self.tree.sync()
                print(f"[{self.client.user}] Synced {len(synced)} slash command(s)")
            except Exception as e:
                print(f"[{self.client.user}] Failed to sync slash commands: {e}")

        # Set status
        status_map = {
            "online": self.discord.Status.online,
            "invisible": self.discord.Status.invisible,
            "idle": self.discord.Status.idle,
            "dnd": self.discord.Status.dnd,
        }
        configured_status = status_map.get(
            self.config.discord_status_str.lower(), self.discord.Status.online
        )

        try:
            await self.client.change_presence(status=configured_status)
            print(f"[{self.client.user}] Status set to {configured_status}")
        except Exception as e:
            if "MessageToDict" in str(e) and "including_default_value_fields" in str(e):
                print(
                    f"[{self.client.user}] Skipping status set due to protobuf version issue (harmless)"
                )
            else:
                print(f"[{self.client.user}] Error setting initial status: {e}")

        # Start cleanup tasks
        self.client.loop.create_task(self.cleanup_forward_cache())
        self.client.loop.create_task(self.cleanup_message_cache())

    async def on_presence_update(self, before, after):
        """Called when a user's presence changes."""
        pass

    async def on_message(self, message):
        """Called when a message is received."""
        if message.author.id == self.client.user.id:
            return

        if message.author.bot:
            if str(
                message.author.id
            ) == ASTEROIDE_BOT_ID and self.config.get_guild_config(
                message.guild.id if message.guild else None,
                "detect-ips",
                self.config.default_detect_ips,
            ):
                await self.handle_asteroide_response(message)
            return

        # Cache message for edit/delete detection
        if message.guild:
            self.message_cache[message.id] = {
                "content": message.content,
                "timestamp": datetime.now(),
            }

            # Log messages
            await asyncio.gather(
                self.logging_handler.log_guild_message(
                    message,
                    self.config.get_guild_config(
                        message.guild.id,
                        "message-log",
                        self.config.default_message_log,
                        message.author.id,
                        message.channel.id,
                    ),
                ),
                self.logging_handler.log_guild_attachments(
                    message,
                    self.config.get_attachment_log_setting(
                        message.guild.id, message.author.id, message.channel.id
                    ),
                ),
                return_exceptions=True,
            )
        else:
            await self.logging_handler.log_dm(message)

        # Handle sync messages
        await self._handle_sync_message(message)

        # Handle commands
        gid = message.guild.id if message.guild else None
        if not self.config.get_guild_config(
            gid,
            "allow-commands",
            self.config.default_allow_commands,
            message.author.id,
            message.channel.id,
        ):
            return

        content, prefix = message.content, self.config.get_prefix(gid)
        if not content.startswith(prefix):
            return

        parts = content[len(prefix) :].split()
        if not parts:
            return

        await self.handle_command(message, parts[0].lower(), parts[1:])

    async def handle_asteroide_response(self, message):
        """Handles Asteroide bot responses for alts tracking."""
        try:
            if not re.search(r"\S+ has \d+ alts:", message.content):
                return
            if parsed_data := self.alts_handler.parse_alts_response(message.content):
                self.alts_handler.store_alts_data(parsed_data)
        except Exception as e:
            print(f"[{self.client.user}] Error handling Asteroide response: {e}")

    async def on_message_edit(self, before, after):
        """Called when a message is edited."""
        if after.author.id == self.client.user.id:
            return

        if (
            not after.guild
            or after.author.bot
            or not self.config.get_guild_config(
                after.guild.id,
                "prevent-editing",
                self.config.default_prevent_editing,
                after.author.id,
                after.channel.id,
            )
        ):
            return

        original_content = self.message_cache.get(after.id, {}).get(
            "content", before.content
        )
        new_content = after.content or ""

        if original_content == new_content:
            return

        if after.id not in self.edit_history:
            self.edit_history[after.id] = {
                "bot_msg": None,
                "all_edits": [new_content],
                "original": original_content,
                "timestamp": datetime.now().isoformat(),
            }
        else:
            self.edit_history[after.id]["all_edits"].append(new_content)

        char_diff = abs(len(new_content) - len(original_content))
        pct_changed = calculate_edit_percentage(original_content, new_content)
        is_typo = is_likely_typo(original_content, new_content)

        should_report = (char_diff >= 3 or pct_changed >= 20) and not is_typo

        if not should_report:
            return

        try:
            history_data = self.edit_history[after.id]
            all_edits = history_data["all_edits"]

            if len(all_edits) == 1:
                edit_info = f"**Edited by <@{after.author.id}>**\n**Original:** {original_content or '*empty*'}\n**Now:** {new_content or '*empty*'}"
            else:
                edit_lines = [f"**Original:** {original_content or '*empty*'}"]
                for i, edit in enumerate(all_edits[:-1], 1):
                    edit_lines.append(f"**Edited {i}:** {edit or '*empty*'}")
                edit_lines.append(f"**Now:** {new_content or '*empty*'}")
                edit_info = f"**Edited by <@{after.author.id}>**\n" + "\n".join(
                    edit_lines
                )

            if history_data["bot_msg"]:
                await history_data["bot_msg"].edit(content=edit_info)
            else:
                bot_msg = await self.bot_send(after.channel, content=edit_info)
                if bot_msg:
                    history_data["bot_msg"] = bot_msg

        except Exception as e:
            error_name = type(e).__name__
            if "Forbidden" in error_name and hasattr(e, 'code') and e.code != 50013:
                print(f"[{self.client.user}] Permission error on_message_edit: {e}")
            else:
                print(f"[{self.client.user}] Error in on_message_edit: {e}")

    async def on_message_delete(self, message):
        """Called when a message is deleted."""
        gid = message.guild.id if message.guild else None
        if gid is not None and not self.config.get_guild_config(
            gid,
            "prevent-deleting",
            self.config.default_prevent_deleting,
            message.author.id,
            message.channel.id,
        ):
            return

        original_content = self.message_cache.get(message.id, {}).get(
            "content", message.content
        )
        final_content = message.content

        content_display = ""
        if original_content and final_content and original_content != final_content:
            content_display = f"**Original:** `{original_content.replace('`', '`')}`\n**Final:** `{final_content.replace('`', '`')}`"
        else:
            content_display = f"`{(final_content or original_content or '[Empty Message]').replace('`', '`')}`"

        attachments_text = (
            "\n\n**Attachments:**\n"
            + "\n".join([f"<{att.url}>" for att in message.attachments])
            if message.attachments
            else ""
        )

        if not original_content and not final_content and not attachments_text:
            return

        content_to_send = (
            f"{content_display}\ndeleted by <@{message.author.id}>{attachments_text}"
        )

        try:
            await self.bot_send(message.channel, content=content_to_send)
        except Exception as e:
            error_name = type(e).__name__
            if "Forbidden" in error_name and hasattr(e, 'code') and e.code != 50013:
                print(f"[{self.client.user}] Permission error on_message_delete: {e}")
            else:
                print(f"[{self.client.user}] Error in on_message_delete: {e}")
        finally:
            if message.id in self.message_cache:
                del self.message_cache[message.id]
            if message.id in self.edit_history:
                del self.edit_history[message.id]

    async def _handle_sync_message(self, message):
        """Handles message syncing to configured channel."""
        if not self.config.sync_channel_id or (
            message.guild and str(message.channel.id) == self.config.sync_channel_id
        ):
            return

        is_dm = not message.guild
        is_ping = message.guild and self.client.user in message.mentions
        is_reply = (
            message.reference
            and message.reference.resolved
            and message.reference.resolved.author == self.client.user
        )
        is_keyword = bool(re.search(r"liforra", message.content, re.IGNORECASE))

        if not (is_dm or is_ping or is_reply or is_keyword):
            return

        try:
            target_channel = self.client.get_channel(int(self.config.sync_channel_id))
            if not target_channel:
                return print(
                    f"[{self.client.user}] SYNC ERROR: Cannot find channel ID {self.config.sync_channel_id}"
                )
        except (ValueError, TypeError):
            return print(f"[{self.client.user}] SYNC ERROR: Invalid sync-channel ID.")

        mention_string = (
            f"<@&{self.config.sync_mention_id}>" if self.config.sync_mention_id else ""
        )

        if is_dm:
            sync_content = f"**DM**\nFrom: {message.author} (`{message.author.id}`)\nContent: {message.content or '*No content*'}"
        else:
            m_type = "MENTION" if is_ping else "REPLY" if is_reply else "KEYWORD"
            sync_content = f"**{m_type}**\nFrom: {message.author} (`{message.author.id}`)\nGuild: {message.guild.name}\nChannel: <#{message.channel.id}>\nJump: {message.jump_url}\nContent: {message.content or '*No content*'}"

        if mention_string:
            sync_content = f"{mention_string}\n{sync_content}"

        files_to_send = []
        if message.attachments:
            import httpx

            async with httpx.AsyncClient() as http_client:
                for att in message.attachments:
                    try:
                        response = await http_client.get(att.url, timeout=60)
                        response.raise_for_status()
                        import io

                        files_to_send.append(
                            self.discord.File(
                                io.BytesIO(response.content), filename=att.filename
                            )
                        )
                    except Exception as e:
                        sync_content += f"\nAttachment Error: Failed to download `{att.filename}`: {e}"

        try:
            await self.bot_send(
                target_channel, content=sync_content, files=files_to_send
            )
        except Exception as e:
            print(f"[{self.client.user}] SYNC ERROR: {e}")