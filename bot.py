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
from handlers.oauth_handler import OAuthHandler
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
        
        # OAuth is only required for bot tokens
        self.oauth_handler = OAuthHandler() if self.token_type == "bot" else None

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
            "ip": "Usage: `{p}ip <info|db> [args]`\n- `info <ip>`: Fetches live information about an IP address (supports IPv4 and IPv6).\n- `db info <ip>`: Shows cached IP information from the database.\n- `db list [page]`: Lists all IPs in the database.\n- `db search <term>`: Searches IPs by country, city, or ISP.\n- `db refresh`: Updates all cached IP information.\n- `db stats`: Shows database statistics.",
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

    def check_authorization(self, user_id: int) -> bool:
        """
        Checks if a user is authorized via OAuth.
        - This check is only enforced for 'bot' token types.
        - Self-bots bypass this check entirely.
        - All users, including admins, must authorize on bot instances.
        """
        if self.token_type != "bot" or not self.oauth_handler:
            return True
        
        return self.oauth_handler.is_user_authorized(str(user_id))

    def register_slash_commands(self):
        """Registers slash commands for bot tokens."""
        if not self.tree or not self.oauth_handler:
            return

        # Trump command
        @self.tree.command(name="trump", description="Get a random Trump quote")
        @self.app_commands.describe(ephemeral="Show response only to you (default: True)")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def trump_slash(interaction: self.discord.Interaction, ephemeral: bool = True):
            if not self.check_authorization(interaction.user.id):
                await interaction.response.send_message(
                    self.oauth_handler.get_authorization_message(interaction.user.mention),
                    ephemeral=True
                )
                return
            
            await interaction.response.defer(ephemeral=ephemeral)
            
            import httpx
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        "https://api.whatdoestrumpthink.com/api/v1/quotes/random",
                        timeout=10,
                    )
                    response.raise_for_status()
                    quote = response.json().get("message", "Could not retrieve a quote.")
                    await interaction.followup.send(f'"{quote}" ~Donald Trump', ephemeral=ephemeral)
            except Exception as e:
                await interaction.followup.send(f"Sorry, an error occurred: {type(e).__name__}", ephemeral=ephemeral)

        # Websites command
        @self.tree.command(name="websites", description="Check status of configured websites")
        @self.app_commands.describe(ephemeral="Show response only to you (default: True)")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def websites_slash(interaction: self.discord.Interaction, ephemeral: bool = True):
            if not self.check_authorization(interaction.user.id):
                await interaction.response.send_message(
                    self.oauth_handler.get_authorization_message(interaction.user.mention),
                    ephemeral=True
                )
                return
            
            await interaction.response.defer(ephemeral=ephemeral)
            
            gid = interaction.guild.id if interaction.guild else None
            sites = self.config.get_guild_config(gid, "websites", self.config.default_websites, interaction.user.id, interaction.channel.id)
            friend_sites = self.config.get_guild_config(gid, "friend_websites", self.config.default_friend_websites, interaction.user.id, interaction.channel.id)
            
            main_res, friend_res = await asyncio.gather(
                self.user_commands_handler._check_and_format_sites(sites, "Websites"),
                self.user_commands_handler._check_and_format_sites(friend_sites, "Friends Websites"),
            )
            
            final_output = []
            if main_res: final_output.extend(main_res)
            if friend_res:
                if final_output: final_output.append("")
                final_output.extend(friend_res)
            
            content = "\n".join(final_output) if final_output else "No websites are configured."
            await interaction.followup.send(content, ephemeral=ephemeral)

        # IP Info command
        @self.tree.command(name="ip", description="Get information about an IP address")
        @self.app_commands.describe(ip="The IP address to look up (IPv4 or IPv6)", ephemeral="Show response only to you (default: True)")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def ip_slash(interaction: self.discord.Interaction, ip: str, ephemeral: bool = True):
            if not self.check_authorization(interaction.user.id):
                await interaction.response.send_message(
                    self.oauth_handler.get_authorization_message(interaction.user.mention),
                    ephemeral=True
                )
                return
            
            await interaction.response.defer(ephemeral=ephemeral)
            
            from utils.helpers import is_valid_ip, is_valid_ipv6
            from utils.constants import COUNTRY_FLAGS
            
            if not is_valid_ip(ip):
                await interaction.followup.send("❌ Invalid IP address format", ephemeral=ephemeral)
                return
            
            ip_data = await self.ip_handler.fetch_ip_info(ip)
            if not ip_data:
                await interaction.followup.send(f"❌ Failed to fetch info for `{ip}`", ephemeral=ephemeral)
                return
            
            flag = COUNTRY_FLAGS.get(ip_data.get("countryCode", ""), "🌐")
            ip_header = f"**IP Information for `{ip}`:**" if is_valid_ipv6(ip) else f"**IP Information for [{ip}](<https://whatismyipaddress.com/ip/{ip}>):**"
            
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
            
            vpn_provider = self.ip_handler.detect_vpn_provider(ip_data.get("isp", ""), ip_data.get("org", ""))
            if vpn_provider: output.append(f"**VPN Provider:** {vpn_provider}")
            elif ip_data.get("proxy"): output.append(f"**Proxy/VPN:** Yes")
            if ip_data.get("hosting"): output.append(f"**VPS/Hosting:** Yes")
            
            await interaction.followup.send("\n".join(output), ephemeral=ephemeral)

        # Alts command
        @self.tree.command(name="alts", description="Look up a user's known alts and IPs")
        @self.app_commands.describe(username="The username to look up", ephemeral="Show response only to you (default: True)")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def alts_slash(interaction: self.discord.Interaction, username: str, ephemeral: bool = True):
            if not str(interaction.user.id) in self.config.admin_ids:
                await interaction.response.send_message("❌ This command is admin-only.", ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=ephemeral)
            
            from utils.helpers import format_alt_name, format_alts_grid
            
            search_term = username
            found_user = None
            lowercase_map = {k.lower(): k for k in self.alts_handler.alts_data.keys()}
            
            search_candidates = [search_term, f".{search_term}", f"...{search_term}"]
            for candidate in search_candidates:
                if candidate.lower() in lowercase_map:
                    found_user = lowercase_map[candidate.lower()]
                    break
            
            if not found_user:
                await interaction.followup.send(f"❌ No data for `{search_term}`", ephemeral=ephemeral)
                return
            
            data = self.alts_handler.alts_data[found_user]
            alts = sorted(list(data.get("alts", set())))
            ips = sorted(list(data.get("ips", set())))
            
            output = [f"**Alts data for {format_alt_name(found_user)}:**"]
            if alts:
                output.append(f"**Alts ({len(alts)}):**")
                output.extend(format_alts_grid([format_alt_name(alt) for alt in alts], 3))
            if ips:
                output.append(f"\n**IPs ({len(ips)}):**")
                output.extend([f"→ {self.ip_handler.format_ip_with_geo(ip)}" for ip in ips])
            
            output.append(f"\n*First seen: {data.get('first_seen', 'N/A')[:10]} | Last updated: {data.get('last_updated', 'N/A')[:10]}*")
            await interaction.followup.send("\n".join(output), ephemeral=ephemeral)

        # Pings command
        @self.tree.command(name="pings", description="Ping configured devices")
        @self.app_commands.describe(ephemeral="Show response only to you (default: True)")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def pings_slash(interaction: self.discord.Interaction, ephemeral: bool = True):
            if not self.check_authorization(interaction.user.id):
                await interaction.response.send_message(self.oauth_handler.get_authorization_message(interaction.user.mention), ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=ephemeral)
            
            async def _ping(hostname: str):
                try:
                    proc = await asyncio.create_subprocess_exec("ping", "-c", "1", "-W", "1", hostname, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    await proc.wait()
                    return f"- `{hostname.replace('.liforra.de', '')}`: {'Responding' if proc.returncode == 0 else 'Unreachable'}"
                except Exception as e:
                    return f"- `{hostname.replace('.liforra.de', '')}`: Error ({type(e).__name__})"

            devices = ["alhena.liforra.de", "sirius.liforra.de", "chaosserver.liforra.de", "antares.liforra.de"]
            results = ["**Device Ping Status:**", *await asyncio.gather(*[_ping(dev) for dev in devices])]
            await interaction.followup.send("\n".join(results), ephemeral=ephemeral)

        # Help command
        @self.tree.command(name="help", description="Show available commands")
        @self.app_commands.describe(ephemeral="Show response only to you (default: True)")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def help_slash(interaction: self.discord.Interaction, ephemeral: bool = True):
            p = self.config.get_prefix(interaction.guild.id if interaction.guild else None)
            
            help_text = (
                f"**Text Commands:** Use prefix `{p}` followed by:\n`trump`, `websites`, `pings`, `note`, `ip`, `help`\n\n"
                f"**Slash Commands:** Available via `/`:\n`/trump`, `/websites`, `/ip`, `/alts`, `/pings`, `/help`\n\n"
                f"*Type `{p}help <command>` for detailed text command info.*\n"
                f"*All slash commands have an `ephemeral` option (default: True).*\n\n"
            )

            if self.token_type == "bot":
                help_text += f"🔒 **Authorization required** for most commands. [Click here to authorize]({self.oauth_handler.oauth_url})"
            
            if str(interaction.user.id) in self.config.admin_ids:
                admin_cmds = ", ".join(f"`{cmd}`" for cmd in self.admin_commands.keys())
                help_text += f"\n\n**Admin Commands:** {admin_cmds}"
            
            await interaction.response.send_message(help_text, ephemeral=ephemeral)

    async def run(self):
        """Starts the bot."""
        print(f"Starting bot instance ({self.token_type}) in directory: {self.data_dir}")
        self.config.load_config()
        self.alts_handler = AltsHandler(self.data_dir, self.config.default_clean_spigey)
        self.alts_handler.load_and_preprocess_alts_data()
        self.load_notes()

        handler = logging.FileHandler(filename=self.log_file, encoding="utf-8", mode="w")
        try:
            self.discord.utils.setup_logging(handler=handler, root=False)
        except AttributeError:
            logging.basicConfig(handlers=[handler], level=logging.INFO)

        try:
            await self.client.start(self.token)
        except Exception as e:
            if "LoginFailure" in str(type(e).__name__):
                print(f"!!! LOGIN FAILED for {self.token_type} in {self.data_dir}. Check token. !!!")
            else:
                print(f"An unexpected error occurred for {self.token_type} in {self.data_dir}: {e}")

    def load_notes(self):
        if self.notes_file.exists():
            try:
                with open(self.notes_file, "r", encoding="utf-8") as f: self.notes_data = json.load(f)
            except Exception as e:
                print(f"[{self.data_dir.name}] Error loading notes: {e}")
                self.notes_data = {"public": {}, "private": {}}
        else: self.notes_data = {"public": {}, "private": {}}

    def save_notes(self):
        try:
            with open(self.notes_file, "w", encoding="utf-8") as f: json.dump(self.notes_data, f, indent=2, ensure_ascii=False)
        except Exception as e: print(f"[{self.data_dir.name}] Error saving notes: {e}")

    def load_user_tokens(self) -> Dict:
        if not self.user_tokens_file.exists(): return {}
        try:
            with open(self.user_tokens_file, "r", encoding="utf-8") as f: return json.load(f)
        except (json.JSONDecodeError, IOError): return {}

    def save_user_tokens(self, tokens: Dict):
        try:
            with open(self.user_tokens_file, "w", encoding="utf-8") as f: json.dump(tokens, f, indent=4)
        except IOError as e: print(f"[Token Storage] Error saving user tokens: {e}")

    def censor_text(self, text: str, guild_id: Optional[int] = None) -> str:
        if not text or not isinstance(text, str): return text or ""
        allow_swears = self.config.get_guild_config(guild_id, "allow-swears", self.config.default_allow_swears)
        allow_slurs = self.config.get_guild_config(guild_id, "allow-slurs", self.config.default_allow_slurs)
        if not allow_slurs:
            for slur in SLUR_WORDS: text = re.compile(re.escape(slur), re.IGNORECASE).sub("█" * len(slur), text)
        if not allow_swears:
            for swear in SWEAR_WORDS: text = re.compile(re.escape(swear), re.IGNORECASE).sub("*" * len(swear), text)
        return text

    async def bot_send(self, channel, content=None, files=None):
        censored_content = self.censor_text(content, channel.guild.id if hasattr(channel, "guild") and channel.guild else None) if content else ""
        try:
            if not censored_content and not files: return None
            if not censored_content: return await channel.send(files=files, suppress_embeds=True)
            sent_message = None
            for i, chunk in enumerate(split_message(censored_content)):
                msg_files = files if i == 0 else None
                sent = await channel.send(content=chunk, files=msg_files, suppress_embeds=True)
                if i == 0: sent_message = sent
            return sent_message
        except Exception as e:
            if "Forbidden" in type(e).__name__: print(f"[{self.client.user}] Missing permissions in channel {channel.id}")
            else: print(f"[{self.client.user}] Error sending message: {e}")
        return None

    async def cleanup_forward_cache(self):
        await self.client.wait_until_ready()
        while not self.client.is_closed():
            await asyncio.sleep(3600)
            cutoff = datetime.now() - timedelta(hours=24)
            expired = [k for k, v in self.forward_cache.items() if v["timestamp"] < cutoff]
            for k in expired: del self.forward_cache[k]
            if expired: print(f"[{self.client.user}] Cleaned {len(expired)} old forward cache entries.")

    async def cleanup_message_cache(self):
        await self.client.wait_until_ready()
        while not self.client.is_closed():
            await asyncio.sleep(600)
            now = datetime.now()
            cutoff = now - timedelta(minutes=10)
            msg_expired = [k for k, v in self.message_cache.items() if v["timestamp"] < cutoff]
            for k in msg_expired: del self.message_cache[k]
            if msg_expired: print(f"[{self.client.user}] Cleaned {len(msg_expired)} old message cache entries.")
            
            edit_expired = [k for k, v in self.edit_history.items() if now - datetime.fromisoformat(v.get("timestamp", now.isoformat())) > timedelta(minutes=10)]
            for k in edit_expired: del self.edit_history[k]
            if edit_expired: print(f"[{self.client.user}] Cleaned {len(edit_expired)} old edit history entries.")

    async def handle_command(self, message, command_name: str, args: list):
        if not self.check_authorization(message.author.id):
            if self.oauth_handler:
                await self.bot_send(message.channel, self.oauth_handler.get_authorization_message(message.author.mention))
            return
        
        try:
            if command_name in self.user_commands:
                await self.user_commands[command_name](message, args)
            elif command_name in self.admin_commands and str(message.author.id) in self.config.admin_ids:
                await self.admin_commands[command_name](message, args)
        except Exception as e:
            print(f"[{self.client.user}] Error in command '{command_name}': {e}")
            import traceback; traceback.print_exc()

    async def on_ready(self):
        print(f"Logged in as {self.client.user} (ID: {self.client.user.id}) [Type: {self.token_type}]")
        if self.token_type == "bot" and self.tree:
            try:
                print(f"[{self.client.user}] Syncing slash commands...")
                synced = await self.tree.sync()
                print(f"[{self.client.user}] Synced {len(synced)} slash command(s)")
            except Exception as e: print(f"[{self.client.user}] Failed to sync slash commands: {e}")
        
        status_map = {"online": self.discord.Status.online, "invisible": self.discord.Status.invisible, "idle": self.discord.Status.idle, "dnd": self.discord.Status.dnd}
        status = status_map.get(self.config.discord_status_str.lower(), self.discord.Status.online)
        try:
            await self.client.change_presence(status=status)
            print(f"[{self.client.user}] Status set to {status}")
        except Exception as e:
            if "MessageToDict" in str(e) and "including_default_value_fields" in str(e):
                print(f"[{self.client.user}] Skipping status set (harmless protobuf issue)")
            else:
                print(f"[{self.client.user}] Error setting status: {e}")

        self.client.loop.create_task(self.cleanup_forward_cache())
        self.client.loop.create_task(self.cleanup_message_cache())

    async def on_presence_update(self, before, after): pass

    async def on_message(self, message):
        if message.author.id == self.client.user.id: return
        if message.author.bot:
            if str(message.author.id) == ASTEROIDE_BOT_ID and self.config.get_guild_config(message.guild.id if message.guild else None, "detect-ips", self.config.default_detect_ips):
                await self.handle_asteroide_response(message)
            return

        if message.guild:
            self.message_cache[message.id] = {"content": message.content, "timestamp": datetime.now()}
            await asyncio.gather(
                self.logging_handler.log_guild_message(message, self.config.get_guild_config(message.guild.id, "message-log", self.config.default_message_log, message.author.id, message.channel.id)),
                self.logging_handler.log_guild_attachments(message, self.config.get_attachment_log_setting(message.guild.id, message.author.id, message.channel.id)),
                return_exceptions=True
            )
        else: await self.logging_handler.log_dm(message)

        await self._handle_sync_message(message)

        gid = message.guild.id if message.guild else None
        if not self.config.get_guild_config(gid, "allow-commands", self.config.default_allow_commands, message.author.id, message.channel.id): return

        prefix = self.config.get_prefix(gid)
        if not message.content.startswith(prefix): return
        
        parts = message.content[len(prefix):].split()
        if not parts: return
        await self.handle_command(message, parts[0].lower(), parts[1:])

    async def handle_asteroide_response(self, message):
        try:
            if not re.search(r"\S+ has \d+ alts:", message.content): return
            if parsed := self.alts_handler.parse_alts_response(message.content): self.alts_handler.store_alts_data(parsed)
        except Exception as e: print(f"[{self.client.user}] Error handling Asteroide response: {e}")

    async def on_message_edit(self, before, after):
        if after.author.id == self.client.user.id or not after.guild or after.author.bot: return
        if not self.config.get_guild_config(after.guild.id, "prevent-editing", self.config.default_prevent_editing, after.author.id, after.channel.id): return

        original = self.message_cache.get(after.id, {}).get("content", before.content)
        new = after.content or ""
        if original == new: return

        if after.id not in self.edit_history: self.edit_history[after.id] = {"bot_msg": None, "all_edits": [], "original": original, "timestamp": datetime.now().isoformat()}
        self.edit_history[after.id]["all_edits"].append(new)
        
        if not ((abs(len(new) - len(original)) >= 3 or calculate_edit_percentage(original, new) >= 20) and not is_likely_typo(original, new)): return

        try:
            history = self.edit_history[after.id]
            edit_lines = [f"**Original:** {original or '*empty*'}"] + [f"**Edited {i+1}:** {e or '*empty*'}" for i, e in enumerate(history['all_edits'][:-1])] + [f"**Now:** {new or '*empty*'}" ]
            edit_info = f"**Edited by <@{after.author.id}>**\n" + "\n".join(edit_lines[0:1] + edit_lines[-1:] if len(edit_lines) <= 2 else edit_lines)

            if history["bot_msg"]: await history["bot_msg"].edit(content=edit_info)
            else:
                bot_msg = await self.bot_send(after.channel, content=edit_info)
                if bot_msg: history["bot_msg"] = bot_msg
        except Exception as e: print(f"[{self.client.user}] Error in on_message_edit: {e}")

    async def on_message_delete(self, message):
        gid = message.guild.id if message.guild else None
        if gid is not None and not self.config.get_guild_config(gid, "prevent-deleting", self.config.default_prevent_deleting, message.author.id, message.channel.id): return

        original = self.message_cache.get(message.id, {}).get("content", message.content)
        final = message.content
        
        content_display = f"`{(final or original or '[Empty Message]').replace('`', '`')}`"
        if original and final and original != final:
            content_display = f"**Original:** `{original.replace('`', '`')}`\n**Final:** `{final.replace('`', '`')}`"

        attachments = "\n\n**Attachments:**\n" + "\n".join([f"<{att.url}>" for att in message.attachments]) if message.attachments else ""
        if not original and not final and not attachments: return

        try: await self.bot_send(message.channel, f"{content_display}\ndeleted by <@{message.author.id}>{attachments}")
        except Exception as e: print(f"[{self.client.user}] Error in on_message_delete: {e}")
        finally:
            if message.id in self.message_cache: del self.message_cache[message.id]
            if message.id in self.edit_history: del self.edit_history[message.id]

    async def _handle_sync_message(self, message):
        if not self.config.sync_channel_id or (message.guild and str(message.channel.id) == self.config.sync_channel_id): return
        
        is_dm = not message.guild
        is_ping = message.guild and self.client.user in message.mentions
        is_reply = message.reference and message.reference.resolved and message.reference.resolved.author == self.client.user
        is_keyword = bool(re.search(r"liforra", message.content, re.IGNORECASE))
        if not (is_dm or is_ping or is_reply or is_keyword): return

        try: target_channel = self.client.get_channel(int(self.config.sync_channel_id))
        except (ValueError, TypeError): return print(f"[{self.client.user}] SYNC ERROR: Invalid sync-channel ID.")
        if not target_channel: return print(f"[{self.client.user}] SYNC ERROR: Cannot find channel ID {self.config.sync_channel_id}")

        mention = f"<@&{self.config.sync_mention_id}>" if self.config.sync_mention_id else ""
        if is_dm:
            sync_content = f"**DM**\nFrom: {message.author} (`{message.author.id}`)\nContent: {message.content or '*No content*'}"
        else:
            m_type = "MENTION" if is_ping else "REPLY" if is_reply else "KEYWORD"
            sync_content = f"**{m_type}**\nFrom: {message.author} (`{message.author.id}`)\nGuild: {message.guild.name}\nChannel: <#{message.channel.id}>\nJump: {message.jump_url}\nContent: {message.content or '*No content*'}"

        if mention: sync_content = f"{mention}\n{sync_content}"
        
        files = []
        if message.attachments:
            import httpx, io
            async with httpx.AsyncClient() as http_client:
                for att in message.attachments:
                    try:
                        res = await http_client.get(att.url, timeout=60); res.raise_for_status()
                        files.append(self.discord.File(io.BytesIO(res.content), filename=att.filename))
                    except Exception as e: sync_content += f"\nAttachment Error: `{att.filename}`: {e}"
        try: await self.bot_send(target_channel, content=sync_content, files=files)
        except Exception as e: print(f"[{self.client.user}] SYNC ERROR: {e}")