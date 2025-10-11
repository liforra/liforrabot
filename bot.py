"""Main Bot class with event handlers."""

import asyncio
import re
import json
import logging
from pathlib import Path
from typing import Dict, Optional, Union, List
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

        # Command mappings (for selfbots only)
        self.user_commands = {
            "trump": self.user_commands_handler.command_trump,
            "websites": self.user_commands_handler.command_websites,
            "pings": self.user_commands_handler.command_pings,
            "note": self.user_commands_handler.command_note,
            "help": self.user_commands_handler.command_help,
            "ip": self.user_commands_handler.command_ip,
            "playerinfo": self.user_commands_handler.command_playerinfo,
            "alts": self.user_commands_handler.command_alts,
        }
        self.admin_commands = {
            "reload-config": self.admin_commands_handler.command_reload_config,
            "config": self.admin_commands_handler.command_config,
            "resend": self.admin_commands_handler.command_resend,
            "override": self.admin_commands_handler.command_override,
            "alts": self.admin_commands_handler.command_alts,
            "qrlogin": self.admin_commands_handler.command_qrlogin,
        }

        # Help texts (for selfbots)
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

        import httpx
        from utils.helpers import format_alt_name, format_alts_grid, is_valid_ip, is_valid_ipv6
        from utils.constants import COUNTRY_FLAGS

        # ==================== PAGINATION VIEW ====================
        class PaginationView(self.discord.ui.View):
            def __init__(self, embeds: List[self.discord.Embed], user_id: int, timeout: int = 180):
                super().__init__(timeout=timeout)
                self.embeds = embeds
                self.user_id = user_id
                self.current_page = 0
                self.message = None
                self.update_buttons()

            def update_buttons(self):
                self.first_page.disabled = self.current_page == 0
                self.prev_page.disabled = self.current_page == 0
                self.next_page.disabled = self.current_page >= len(self.embeds) - 1
                self.last_page.disabled = self.current_page >= len(self.embeds) - 1

            async def interaction_check(self, interaction: self.discord.Interaction) -> bool:
                if interaction.user.id != self.user_id:
                    await interaction.response.send_message("❌ This is not your pagination menu!", ephemeral=True)
                    return False
                return True

            @self.discord.ui.button(label="⏮️", style=self.discord.ButtonStyle.gray)
            async def first_page(self, interaction: self.discord.Interaction, button: self.discord.ui.Button):
                self.current_page = 0
                self.update_buttons()
                await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

            @self.discord.ui.button(label="◀️", style=self.discord.ButtonStyle.primary)
            async def prev_page(self, interaction: self.discord.Interaction, button: self.discord.ui.Button):
                self.current_page = max(0, self.current_page - 1)
                self.update_buttons()
                await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

            @self.discord.ui.button(label="🗑️", style=self.discord.ButtonStyle.danger)
            async def delete_msg(self, interaction: self.discord.Interaction, button: self.discord.ui.Button):
                await interaction.message.delete()
                self.stop()

            @self.discord.ui.button(label="▶️", style=self.discord.ButtonStyle.primary)
            async def next_page(self, interaction: self.discord.Interaction, button: self.discord.ui.Button):
                self.current_page = min(len(self.embeds) - 1, self.current_page + 1)
                self.update_buttons()
                await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

            @self.discord.ui.button(label="⏭️", style=self.discord.ButtonStyle.gray)
            async def last_page(self, interaction: self.discord.Interaction, button: self.discord.ui.Button):
                self.current_page = len(self.embeds) - 1
                self.update_buttons()
                await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

            async def on_timeout(self):
                for item in self.children:
                    item.disabled = True
                if self.message:
                    try:
                        await self.message.edit(view=self)
                    except:
                        pass

        # ==================== USER COMMANDS ====================
        
        # Trump command
        @self.tree.command(name="trump", description="Get a random Trump quote")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def trump_slash(interaction: self.discord.Interaction, _ephemeral: bool = False):
            if not self.check_authorization(interaction.user.id):
                await interaction.response.send_message(
                    self.oauth_handler.get_authorization_message(interaction.user.mention),
                    ephemeral=True
                )
                return
            
            await interaction.response.defer(ephemeral=_ephemeral)
            
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        "https://api.whatdoestrumpthink.com/api/v1/quotes/random",
                        timeout=10,
                    )
                    response.raise_for_status()
                    quote = response.json().get("message", "Could not retrieve a quote.")
                    
                    embed = self.discord.Embed(
                        title="🇺🇸 Trump Quote",
                        description=f'"{quote}"',
                        color=0xE74C3C,
                        timestamp=datetime.now()
                    )
                    embed.set_footer(text="~Donald Trump", icon_url="https://upload.wikimedia.org/wikipedia/commons/thumb/5/56/Donald_Trump_official_portrait.jpg/220px-Donald_Trump_official_portrait.jpg")
                    
                    await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
            except Exception as e:
                await interaction.followup.send(f"❌ Sorry, an error occurred: {type(e).__name__}", ephemeral=_ephemeral)

        @self.tree.command(name="ptrump", description="[Private] Get a random Trump quote")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def ptrump_slash(interaction: self.discord.Interaction):
            await trump_slash(interaction, _ephemeral=True)

        # Websites command
        @self.tree.command(name="websites", description="Check status of configured websites")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def websites_slash(interaction: self.discord.Interaction, _ephemeral: bool = False):
            if not self.check_authorization(interaction.user.id):
                await interaction.response.send_message(
                    self.oauth_handler.get_authorization_message(interaction.user.mention),
                    ephemeral=True
                )
                return
            
            await interaction.response.defer(ephemeral=_ephemeral)
            
            gid = interaction.guild.id if interaction.guild else None
            sites = self.config.get_guild_config(gid, "websites", self.config.default_websites, interaction.user.id, interaction.channel.id)
            friend_sites = self.config.get_guild_config(gid, "friend_websites", self.config.default_friend_websites, interaction.user.id, interaction.channel.id)
            
            embed = self.discord.Embed(
                title="🌐 Website Status",
                description="Checking configured websites...",
                color=0x3498DB,
                timestamp=datetime.now()
            )
            
            if sites:
                async with httpx.AsyncClient() as client:
                    responses = await asyncio.gather(
                        *[client.head(site, timeout=10) for site in sites],
                        return_exceptions=True,
                    )
                    
                    site_status = []
                    for site, resp in zip(sites, responses):
                        if isinstance(resp, httpx.Response) and 200 <= resp.status_code < 400:
                            site_status.append(f"🟢 [{site}]({site})")
                        else:
                            site_status.append(f"🔴 [{site}]({site})")
                    
                    embed.add_field(
                        name="📌 Your Websites",
                        value="\n".join(site_status) if site_status else "No websites configured",
                        inline=False
                    )
            
            if friend_sites:
                async with httpx.AsyncClient() as client:
                    responses = await asyncio.gather(
                        *[client.head(site, timeout=10) for site in friend_sites],
                        return_exceptions=True,
                    )
                    
                    friend_status = []
                    for site, resp in zip(friend_sites, responses):
                        if isinstance(resp, httpx.Response) and 200 <= resp.status_code < 400:
                            friend_status.append(f"🟢 [{site}]({site})")
                        else:
                            friend_status.append(f"🔴 [{site}]({site})")
                    
                    embed.add_field(
                        name="👥 Friends' Websites",
                        value="\n".join(friend_status) if friend_status else "No websites configured",
                        inline=False
                    )
            
            if not sites and not friend_sites:
                embed.description = "❌ No websites are configured."
            else:
                embed.description = None
            
            embed.set_footer(text=f"Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
            await interaction.followup.send(embed=embed, ephemeral=_ephemeral)

        @self.tree.command(name="pwebsites", description="[Private] Check status of configured websites")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def pwebsites_slash(interaction: self.discord.Interaction):
            await websites_slash(interaction, _ephemeral=True)

        # Pings command
        @self.tree.command(name="pings", description="Ping configured devices")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def pings_slash(interaction: self.discord.Interaction, _ephemeral: bool = False):
            if not self.check_authorization(interaction.user.id):
                await interaction.response.send_message(self.oauth_handler.get_authorization_message(interaction.user.mention), ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=_ephemeral)
            
            async def _ping(hostname: str):
                try:
                    proc = await asyncio.create_subprocess_exec("ping", "-c", "1", "-W", "1", hostname, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    await proc.wait()
                    status = "🟢 Responding" if proc.returncode == 0 else "🔴 Unreachable"
                    return f"`{hostname.replace('.liforra.de', '')}` - {status}"
                except Exception as e:
                    return f"`{hostname.replace('.liforra.de', '')}` - ⚠️ Error"

            devices = ["alhena.liforra.de", "sirius.liforra.de", "chaosserver.liforra.de", "antares.liforra.de"]
            results = await asyncio.gather(*[_ping(dev) for dev in devices])
            
            embed = self.discord.Embed(
                title="🖥️ Device Ping Status",
                description="\n".join(results),
                color=0x2ECC71,
                timestamp=datetime.now()
            )
            embed.set_footer(text=f"Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
            
            await interaction.followup.send(embed=embed, ephemeral=_ephemeral)

        @self.tree.command(name="ppings", description="[Private] Ping configured devices")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def ppings_slash(interaction: self.discord.Interaction):
            await pings_slash(interaction, _ephemeral=True)

        # IP Info command
        @self.tree.command(name="ip", description="Get information about an IP address")
        @self.app_commands.describe(address="The IP address to look up (IPv4 or IPv6)")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def ip_slash(interaction: self.discord.Interaction, address: str, _ephemeral: bool = False):
            if not self.check_authorization(interaction.user.id):
                await interaction.response.send_message(
                    self.oauth_handler.get_authorization_message(interaction.user.mention),
                    ephemeral=True
                )
                return
            
            await interaction.response.defer(ephemeral=_ephemeral)
            
            if not is_valid_ip(address):
                await interaction.followup.send("❌ Invalid IP address format", ephemeral=_ephemeral)
                return
            
            ip_data = await self.ip_handler.fetch_ip_info(address)
            if not ip_data:
                await interaction.followup.send(f"❌ Failed to fetch info for `{address}`", ephemeral=_ephemeral)
                return
            
            flag = COUNTRY_FLAGS.get(ip_data.get("countryCode", ""), "🌐")
            
            embed = self.discord.Embed(
                title=f"{flag} IP Information",
                description=f"**IP Address:** `{address}`",
                color=0x9B59B6,
                timestamp=datetime.now()
            )
            
            embed.add_field(name="🌍 Country", value=f"{ip_data.get('country', 'N/A')} ({ip_data.get('countryCode', 'N/A')})", inline=True)
            embed.add_field(name="📍 Region", value=ip_data.get('regionName', 'N/A'), inline=True)
            embed.add_field(name="🏙️ City", value=ip_data.get('city', 'N/A'), inline=True)
            embed.add_field(name="📮 ZIP", value=ip_data.get('zip', 'N/A'), inline=True)
            embed.add_field(name="🕐 Timezone", value=ip_data.get('timezone', 'N/A'), inline=True)
            embed.add_field(name="📡 ISP", value=ip_data.get('isp', 'N/A'), inline=True)
            embed.add_field(name="🏢 Organization", value=ip_data.get('org', 'N/A'), inline=False)
            
            vpn_provider = self.ip_handler.detect_vpn_provider(ip_data.get("isp", ""), ip_data.get("org", ""))
            security_info = []
            if vpn_provider:
                security_info.append(f"🔒 **VPN Provider:** {vpn_provider}")
            elif ip_data.get("proxy"):
                security_info.append("🔒 **Proxy/VPN:** Yes")
            if ip_data.get("hosting"):
                security_info.append("☁️ **VPS/Hosting:** Yes")
            
            if security_info:
                embed.add_field(name="🔐 Security Info", value="\n".join(security_info), inline=False)
            
            if not is_valid_ipv6(address):
                embed.add_field(name="🔗 Links", value=f"[View on WhatIsMyIP](https://whatismyipaddress.com/ip/{address})", inline=False)
            
            embed.set_footer(text=f"Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
            
            await interaction.followup.send(embed=embed, ephemeral=_ephemeral)

        @self.tree.command(name="pip", description="[Private] Get information about an IP address")
        @self.app_commands.describe(address="The IP address to look up (IPv4 or IPv6)")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def pip_slash(interaction: self.discord.Interaction, address: str):
            await ip_slash(interaction, address, _ephemeral=True)

        # IP DB List command with pagination
        @self.tree.command(name="ipdblist", description="List all cached IPs in database")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def ipdblist_slash(interaction: self.discord.Interaction, _ephemeral: bool = False):
            if not self.check_authorization(interaction.user.id):
                await interaction.response.send_message(
                    self.oauth_handler.get_authorization_message(interaction.user.mention),
                    ephemeral=True
                )
                return
            
            await interaction.response.defer(ephemeral=_ephemeral)
            
            per_page = 15
            ips = sorted(self.ip_handler.ip_geo_data.keys())
            total_pages = (len(ips) + per_page - 1) // per_page
            
            if not ips:
                embed = self.discord.Embed(
                    title="📊 IP Database",
                    description="❌ No IPs in database",
                    color=0xE74C3C
                )
                await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
                return
            
            embeds = []
            for page in range(total_pages):
                start = page * per_page
                page_ips = ips[start : start + per_page]
                
                embed = self.discord.Embed(
                    title="📊 Cached IP Database",
                    description=f"Showing page {page + 1} of {total_pages} ({len(ips)} total IPs)",
                    color=0x3498DB,
                    timestamp=datetime.now()
                )
                
                ip_list = []
                for ip in page_ips:
                    ip_list.append(self.ip_handler.format_ip_with_geo(ip))
                
                embed.add_field(name="🌐 IP Addresses", value="\n".join(ip_list), inline=False)
                embed.set_footer(text=f"Page {page + 1}/{total_pages} • Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
                embeds.append(embed)
            
            if len(embeds) == 1:
                await interaction.followup.send(embed=embeds[0], ephemeral=_ephemeral)
            else:
                view = PaginationView(embeds, interaction.user.id)
                message = await interaction.followup.send(embed=embeds[0], view=view, ephemeral=_ephemeral)
                view.message = message

        @self.tree.command(name="pipdblist", description="[Private] List all cached IPs in database")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def pipdblist_slash(interaction: self.discord.Interaction):
            await ipdblist_slash(interaction, _ephemeral=True)

        # IP DB Search command
        @self.tree.command(name="ipdbsearch", description="Search IPs by country, city, or ISP")
        @self.app_commands.describe(term="Search term")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def ipdbsearch_slash(interaction: self.discord.Interaction, term: str, _ephemeral: bool = False):
            if not self.check_authorization(interaction.user.id):
                await interaction.response.send_message(
                    self.oauth_handler.get_authorization_message(interaction.user.mention),
                    ephemeral=True
                )
                return
            
            await interaction.response.defer(ephemeral=_ephemeral)
            
            search_term = term.lower()
            results = []

            for ip, geo in self.ip_handler.ip_geo_data.items():
                searchable = " ".join(
                    [
                        geo.get("country", ""),
                        geo.get("regionName", ""),
                        geo.get("city", ""),
                        geo.get("isp", ""),
                        geo.get("org", ""),
                    ]
                ).lower()

                if search_term in searchable:
                    results.append(ip)

            if not results:
                embed = self.discord.Embed(
                    title="🔍 IP Search Results",
                    description=f"❌ No IPs found matching `{term}`",
                    color=0xE74C3C
                )
                await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
                return
            
            per_page = 15
            total_pages = (len(results) + per_page - 1) // per_page
            
            embeds = []
            for page in range(total_pages):
                start = page * per_page
                page_results = results[start : start + per_page]
                
                embed = self.discord.Embed(
                    title=f"🔍 Search Results for '{term}'",
                    description=f"Found {len(results)} matching IPs (page {page + 1}/{total_pages})",
                    color=0x2ECC71,
                    timestamp=datetime.now()
                )
                
                ip_list = []
                for ip in page_results:
                    ip_list.append(self.ip_handler.format_ip_with_geo(ip))
                
                embed.add_field(name="🌐 Matching IPs", value="\n".join(ip_list), inline=False)
                embed.set_footer(text=f"Page {page + 1}/{total_pages} • Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
                embeds.append(embed)
            
            if len(embeds) == 1:
                await interaction.followup.send(embed=embeds[0], ephemeral=_ephemeral)
            else:
                view = PaginationView(embeds, interaction.user.id)
                message = await interaction.followup.send(embed=embeds[0], view=view, ephemeral=_ephemeral)
                view.message = message

        @self.tree.command(name="pipdbsearch", description="[Private] Search IPs by country, city, or ISP")
        @self.app_commands.describe(term="Search term")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def pipdbsearch_slash(interaction: self.discord.Interaction, term: str):
            await ipdbsearch_slash(interaction, term, _ephemeral=True)

        # IP DB Stats command
        @self.tree.command(name="ipdbstats", description="Show IP database statistics")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def ipdbstats_slash(interaction: self.discord.Interaction, _ephemeral: bool = False):
            if not self.check_authorization(interaction.user.id):
                await interaction.response.send_message(
                    self.oauth_handler.get_authorization_message(interaction.user.mention),
                    ephemeral=True
                )
                return
            
            total_ips = len(self.ip_handler.ip_geo_data)
            countries = set()
            vpn_count = 0
            hosting_count = 0

            for geo in self.ip_handler.ip_geo_data.values():
                if geo.get("countryCode"):
                    countries.add(geo["countryCode"])
                
                vpn_provider = self.ip_handler.detect_vpn_provider(
                    geo.get("isp", ""), geo.get("org", "")
                )
                if vpn_provider or geo.get("proxy"):
                    vpn_count += 1
                
                if geo.get("hosting"):
                    hosting_count += 1

            embed = self.discord.Embed(
                title="📊 IP Database Statistics",
                color=0x3498DB,
                timestamp=datetime.now()
            )
            
            embed.add_field(name="🌐 Total IPs", value=f"`{total_ips}`", inline=True)
            embed.add_field(name="🗺️ Unique Countries", value=f"`{len(countries)}`", inline=True)
            embed.add_field(name="🔒 VPN/Proxy IPs", value=f"`{vpn_count}`", inline=True)
            embed.add_field(name="☁️ VPS/Hosting IPs", value=f"`{hosting_count}`", inline=True)
            
            embed.set_footer(text=f"Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)

            await interaction.response.send_message(embed=embed, ephemeral=_ephemeral)

        @self.tree.command(name="pipdbstats", description="[Private] Show IP database statistics")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def pipdbstats_slash(interaction: self.discord.Interaction):
            await ipdbstats_slash(interaction, _ephemeral=True)

        # IP DB Info command
        @self.tree.command(name="ipdbinfo", description="Get cached information about an IP from database")
        @self.app_commands.describe(address="The IP address to look up")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def ipdbinfo_slash(interaction: self.discord.Interaction, address: str, _ephemeral: bool = False):
            if not self.check_authorization(interaction.user.id):
                await interaction.response.send_message(
                    self.oauth_handler.get_authorization_message(interaction.user.mention),
                    ephemeral=True
                )
                return
            
            if address not in self.ip_handler.ip_geo_data:
                embed = self.discord.Embed(
                    title="❌ Not Found",
                    description=f"No cached data for `{address}` in database",
                    color=0xE74C3C
                )
                await interaction.response.send_message(embed=embed, ephemeral=_ephemeral)
                return

            geo = self.ip_handler.ip_geo_data[address]
            flag = COUNTRY_FLAGS.get(geo.get("countryCode", ""), "🌐")

            embed = self.discord.Embed(
                title=f"{flag} Cached IP Information",
                description=f"**IP Address:** `{address}`",
                color=0x9B59B6,
                timestamp=datetime.now()
            )
            
            embed.add_field(name="🌍 Country", value=f"{geo.get('country', 'N/A')} ({geo.get('countryCode', 'N/A')})", inline=True)
            embed.add_field(name="📍 Region", value=geo.get('regionName', 'N/A'), inline=True)
            embed.add_field(name="🏙️ City", value=geo.get('city', 'N/A'), inline=True)
            embed.add_field(name="📡 ISP", value=geo.get('isp', 'N/A'), inline=False)
            embed.add_field(name="🏢 Organization", value=geo.get('org', 'N/A'), inline=False)

            vpn_provider = self.ip_handler.detect_vpn_provider(
                geo.get("isp", ""), geo.get("org", "")
            )

            security_info = []
            if vpn_provider:
                security_info.append(f"🔒 **VPN Provider:** {vpn_provider}")
            elif geo.get("proxy"):
                security_info.append("🔒 **Proxy/VPN:** Yes")
            if geo.get("hosting"):
                security_info.append("☁️ **VPS/Hosting:** Yes")
            
            if security_info:
                embed.add_field(name="🔐 Security Info", value="\n".join(security_info), inline=False)

            last_updated = geo.get('last_updated', 'N/A')[:10]
            embed.add_field(name="🕐 Last Updated", value=last_updated, inline=False)
            
            embed.set_footer(text=f"Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)

            await interaction.response.send_message(embed=embed, ephemeral=_ephemeral)

        @self.tree.command(name="pipdbinfo", description="[Private] Get cached information about an IP from database")
        @self.app_commands.describe(address="The IP address to look up")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def pipdbinfo_slash(interaction: self.discord.Interaction, address: str):
            await ipdbinfo_slash(interaction, address, _ephemeral=True)

        # IP DB Refresh command (admin only)
        @self.tree.command(name="ipdbrefresh", description="[ADMIN] Refresh all IP geolocation data")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def ipdbrefresh_slash(interaction: self.discord.Interaction, _ephemeral: bool = False):
            if not str(interaction.user.id) in self.config.admin_ids:
                await interaction.response.send_message("❌ This command is admin-only.", ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=_ephemeral)
            
            all_ips = list(self.ip_handler.ip_geo_data.keys())

            if not all_ips:
                embed = self.discord.Embed(
                    title="❌ Error",
                    description="No IPs in database to refresh",
                    color=0xE74C3C
                )
                await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
                return

            geo_results = await self.ip_handler.fetch_ip_info_batch(all_ips)

            timestamp = datetime.now().isoformat()
            for ip, geo_data in geo_results.items():
                self.ip_handler.ip_geo_data[ip] = {
                    "country": geo_data.get("country"),
                    "countryCode": geo_data.get("countryCode"),
                    "region": geo_data.get("region"),
                    "regionName": geo_data.get("regionName"),
                    "city": geo_data.get("city"),
                    "isp": geo_data.get("isp"),
                    "org": geo_data.get("org"),
                    "proxy": geo_data.get("proxy", False),
                    "hosting": geo_data.get("hosting", False),
                    "last_updated": timestamp,
                }

            self.ip_handler.save_ip_geo_data()

            embed = self.discord.Embed(
                title="✅ Refresh Complete",
                description=f"Successfully refreshed `{len(geo_results)}` IP records",
                color=0x2ECC71,
                timestamp=datetime.now()
            )
            embed.set_footer(text=f"Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)

            await interaction.followup.send(embed=embed, ephemeral=_ephemeral)

        @self.tree.command(name="pipdbrefresh", description="[Private] [ADMIN] Refresh all IP geolocation data")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def pipdbrefresh_slash(interaction: self.discord.Interaction):
            await ipdbrefresh_slash(interaction, _ephemeral=True)

        # PlayerInfo command
        @self.tree.command(name="playerinfo", description="Get detailed information about a Minecraft player")
        @self.app_commands.describe(username="The Minecraft username to look up")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def playerinfo_slash(interaction: self.discord.Interaction, username: str, _ephemeral: bool = False):
            if not self.check_authorization(interaction.user.id):
                await interaction.response.send_message(
                    self.oauth_handler.get_authorization_message(interaction.user.mention),
                    ephemeral=True
                )
                return
            
            await interaction.response.defer(ephemeral=_ephemeral)
            
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"https://playerdb.co/api/player/minecraft/{username}",
                        headers={"User-Agent": "https://liforra.de"},
                        timeout=10
                    )
                    response.raise_for_status()
                    data = response.json()
                    
                    if data.get("code") != "player.found":
                        embed = self.discord.Embed(
                            title="❌ Player Not Found",
                            description=f"Could not find Minecraft player `{username}`",
                            color=0xE74C3C
                        )
                        await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
                        return
                    
                    player = data["data"]["player"]
                    
                    # Create beautiful embed
                    embed = self.discord.Embed(
                        title=f"🎮 {player['username']}",
                        description=f"Detailed information about Minecraft player **{player['username']}**",
                        color=0x2ECC71,
                        url=f"https://namemc.com/profile/{player['username']}"
                    )
                    
                    # Set player head as thumbnail
                    embed.set_thumbnail(url=player['avatar'])
                    
                    # UUID Information
                    embed.add_field(
                        name="🆔 UUID",
                        value=f"`{player['id']}`",
                        inline=False
                    )
                    
                    embed.add_field(
                        name="🔢 Raw UUID",
                        value=f"`{player['raw_id']}`",
                        inline=False
                    )
                    
                    # Links
                    links = [
                        f"[NameMC](https://namemc.com/profile/{player['username']})",
                        f"[Avatar]({player['avatar']})",
                        f"[Skin](https://crafatar.com/skins/{player['raw_id']})"
                    ]
                    embed.add_field(
                        name="🔗 Links",
                        value=" • ".join(links),
                        inline=False
                    )
                    
                    # Name History (if available)
                    if player.get('name_history') and len(player['name_history']) > 0:
                        history_list = player['name_history'][:8]
                        history_text = " → ".join([f"`{name}`" for name in history_list])
                        if len(player['name_history']) > 8:
                            history_text += f"\n*... and {len(player['name_history']) - 8} more*"
                        
                        embed.add_field(
                            name="📜 Name History",
                            value=history_text,
                            inline=False
                        )
                    
                    # Skin preview (full body with overlay)
                    skin_render = f"https://mc-heads.net/body/{player['raw_id']}/right"
                    embed.set_image(url=skin_render)
                    
                    # Footer with cache info
                    cached_at = player['meta'].get('cached_at')
                    if cached_at:
                        cached_time = datetime.fromtimestamp(cached_at).strftime('%Y-%m-%d %H:%M:%S UTC')
                        embed.set_footer(
                            text=f"Data cached at {cached_time}",
                            icon_url="https://mc-heads.net/head/MHF_Question/8"
                        )
                    else:
                        embed.set_footer(text="Powered by PlayerDB")
                    
                    await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
                    
            except httpx.HTTPStatusError as e:
                embed = self.discord.Embed(
                    title="❌ API Error",
                    description=f"Failed to fetch player data: HTTP {e.response.status_code}",
                    color=0xE74C3C
                )
                await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
            except Exception as e:
                embed = self.discord.Embed(
                    title="❌ Error",
                    description=f"An unexpected error occurred: {type(e).__name__}",
                    color=0xE74C3C
                )
                await interaction.followup.send(embed=embed, ephemeral=_ephemeral)

        @self.tree.command(name="pplayerinfo", description="[Private] Get detailed information about a Minecraft player")
        @self.app_commands.describe(username="The Minecraft username to look up")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def pplayerinfo_slash(interaction: self.discord.Interaction, username: str):
            await playerinfo_slash(interaction, username, _ephemeral=True)

        # Alts command with pagination
        @self.tree.command(name="alts", description="[ADMIN] Look up a user's known alts and IPs")
        @self.app_commands.describe(username="The username to look up")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def alts_slash(interaction: self.discord.Interaction, username: str, _ephemeral: bool = False):
            await interaction.response.defer(ephemeral=_ephemeral)
            
            if not str(interaction.user.id) in self.config.admin_ids:
                embed = self.discord.Embed(
                    title="❌ Access Denied",
                    description="This command is admin-only.",
                    color=0xE74C3C
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
            
            search_term = username
            found_user = None
            lowercase_map = {k.lower(): k for k in self.alts_handler.alts_data.keys()}
            
            search_candidates = [search_term, f".{search_term}", f"...{search_term}"]
            for candidate in search_candidates:
                if candidate.lower() in lowercase_map:
                    found_user = lowercase_map[candidate.lower()]
                    break
            
            if not found_user:
                embed = self.discord.Embed(
                    title="❌ Not Found",
                    description=f"No data for `{search_term}` in the alts database",
                    color=0xE74C3C
                )
                await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
                return
            
            data = self.alts_handler.alts_data[found_user]
            alts = sorted(list(data.get("alts", set())))
            ips = sorted(list(data.get("ips", set())))
            
            # Create paginated embeds
            embeds = []
            per_page_alts = 20
            per_page_ips = 10
            
            # First embed - overview
            main_embed = self.discord.Embed(
                title=f"👥 Alts Data for {format_alt_name(found_user)}",
                description=f"**Total Alts:** `{len(alts)}` • **Total IPs:** `{len(ips)}`",
                color=0xE67E22,
                timestamp=datetime.now()
            )
            
            main_embed.add_field(
                name="📅 First Seen",
                value=data.get('first_seen', 'N/A')[:10],
                inline=True
            )
            main_embed.add_field(
                name="🔄 Last Updated",
                value=data.get('last_updated', 'N/A')[:10],
                inline=True
            )
            
            main_embed.set_footer(text=f"Page 1 • Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
            embeds.append(main_embed)
            
            # Alts pages
            if alts:
                total_alt_pages = (len(alts) + per_page_alts - 1) // per_page_alts
                for page in range(total_alt_pages):
                    start = page * per_page_alts
                    page_alts = alts[start : start + per_page_alts]
                    
                    embed = self.discord.Embed(
                        title=f"👥 Alts for {format_alt_name(found_user)}",
                        description=f"Showing alts {start + 1}-{start + len(page_alts)} of {len(alts)}",
                        color=0xE67E22,
                        timestamp=datetime.now()
                    )
                    
                    formatted_alts = [format_alt_name(alt) for alt in page_alts]
                    alts_grid = format_alts_grid(formatted_alts, 3)
                    
                    embed.add_field(
                        name="🎮 Known Alts",
                        value="\n".join(alts_grid),
                        inline=False
                    )
                    
                    embed.set_footer(text=f"Page {len(embeds) + 1} (Alts {page + 1}/{total_alt_pages}) • Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
                    embeds.append(embed)
            
            # IPs pages
            if ips:
                total_ip_pages = (len(ips) + per_page_ips - 1) // per_page_ips
                for page in range(total_ip_pages):
                    start = page * per_page_ips
                    page_ips = ips[start : start + per_page_ips]
                    
                    embed = self.discord.Embed(
                        title=f"🌐 IPs for {format_alt_name(found_user)}",
                        description=f"Showing IPs {start + 1}-{start + len(page_ips)} of {len(ips)}",
                        color=0xE67E22,
                        timestamp=datetime.now()
                    )
                    
                    ip_list = []
                    for ip in page_ips:
                        ip_list.append(self.ip_handler.format_ip_with_geo(ip))
                    
                    embed.add_field(
                        name="📍 Known IP Addresses",
                        value="\n".join(ip_list),
                        inline=False
                    )
                    
                    embed.set_footer(text=f"Page {len(embeds) + 1} (IPs {page + 1}/{total_ip_pages}) • Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
                    embeds.append(embed)
            
            if len(embeds) == 1:
                await interaction.followup.send(embed=embeds[0], ephemeral=_ephemeral)
            else:
                view = PaginationView(embeds, interaction.user.id)
                message = await interaction.followup.send(embed=embeds[0], view=view, ephemeral=_ephemeral)
                view.message = message

        @self.tree.command(name="palts", description="[Private] [ADMIN] Look up a user's known alts and IPs")
        @self.app_commands.describe(username="The username to look up")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def palts_slash(interaction: self.discord.Interaction, username: str):
            await alts_slash(interaction, username, _ephemeral=True)

        # Help command
        @self.tree.command(name="help", description="Show available commands")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def help_slash(interaction: self.discord.Interaction, _ephemeral: bool = False):
            embed = self.discord.Embed(
                title="📚 Command Help",
                description="Available slash commands for this bot",
                color=0x3498DB,
                timestamp=datetime.now()
            )
            
            embed.add_field(
                name="🎮 General Commands",
                value=(
                    "`/trump` - Get a random Trump quote\n"
                    "`/websites` - Check website status\n"
                    "`/pings` - Ping configured devices\n"
                    "`/playerinfo <username>` - Get Minecraft player info\n"
                    "`/help` - Show this help message"
                ),
                inline=False
            )
            
            embed.add_field(
                name="🌐 IP Commands",
                value=(
                    "`/ip <address>` - Get live IP information\n"
                    "`/ipdbinfo <address>` - Get cached IP info\n"
                    "`/ipdblist` - List all cached IPs\n"
                    "`/ipdbsearch <term>` - Search IPs\n"
                    "`/ipdbstats` - Show IP database stats"
                ),
                inline=False
            )
            
            if str(interaction.user.id) in self.config.admin_ids:
                embed.add_field(
                    name="🔧 Admin Commands",
                    value=(
                        "`/alts <username>` - Look up player alts and IPs\n"
                        "`/ipdbrefresh` - Refresh all IP data\n"
                        "`/reloadconfig` - Reload all config files\n"
                        "`/configget <path>` - Get a config value\n"
                        "`/configset <path> <value>` - Set a config value\n"
                        "`/configdebug` - Show debug info"
                    ),
                    inline=False
                )
            
            embed.add_field(
                name="💡 Pro Tip",
                value="Prefix any command with `p` (e.g., `/pip`, `/palts`) to make the response private!",
                inline=False
            )
            
            if self.token_type == "bot":
                embed.add_field(
                    name="🔒 Authorization",
                    value=f"Most commands require authorization. [Click here to authorize]({self.oauth_handler.oauth_url})",
                    inline=False
                )
            
            embed.set_footer(text=f"Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
            await interaction.response.send_message(embed=embed, ephemeral=_ephemeral)

        @self.tree.command(name="phelp", description="[Private] Show available commands")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def phelp_slash(interaction: self.discord.Interaction):
            await help_slash(interaction, _ephemeral=True)

        # ==================== ADMIN COMMANDS ====================

        # Reload Config command
        @self.tree.command(name="reloadconfig", description="[ADMIN] Reload all configuration files")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def reloadconfig_slash(interaction: self.discord.Interaction, _ephemeral: bool = False):
            if not str(interaction.user.id) in self.config.admin_ids:
                await interaction.response.send_message("❌ This command is admin-only.", ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=_ephemeral)
            
            try:
                self.config.load_config()
                self.load_notes()
                self.alts_handler.load_and_preprocess_alts_data()
                self.ip_handler.load_ip_geo_data()
                
                embed = self.discord.Embed(
                    title="✅ Reload Complete",
                    description="Successfully reloaded config, notes, alts, and IP data",
                    color=0x2ECC71,
                    timestamp=datetime.now()
                )
                embed.set_footer(text=f"Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
                await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
            except Exception as e:
                embed = self.discord.Embed(
                    title="❌ Reload Failed",
                    description=f"Error: {str(e)}",
                    color=0xE74C3C
                )
                await interaction.followup.send(embed=embed, ephemeral=_ephemeral)

        @self.tree.command(name="preloadconfig", description="[Private] [ADMIN] Reload all configuration files")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def preloadconfig_slash(interaction: self.discord.Interaction):
            await reloadconfig_slash(interaction, _ephemeral=True)

        # Config Get command
        @self.tree.command(name="configget", description="[ADMIN] Get a configuration value")
        @self.app_commands.describe(path="Config path (e.g., general.prefix)")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def configget_slash(interaction: self.discord.Interaction, path: str, _ephemeral: bool = False):
            if not str(interaction.user.id) in self.config.admin_ids:
                await interaction.response.send_message("❌ This command is admin-only.", ephemeral=True)
                return
            
            try:
                value = self.config.config_data
                for key in path.split("."):
                    value = value[key]
                censored_value = self.config.censor_recursive(path, value)
                
                embed = self.discord.Embed(
                    title=f"⚙️ Config: {path}",
                    color=0x3498DB,
                    timestamp=datetime.now()
                )
                
                if isinstance(censored_value, dict):
                    display_str = json.dumps(censored_value, indent=2)
                    embed.description = f"```json\n{display_str}\n```"
                else:
                    embed.description = f"`{censored_value}`"
                
                embed.set_footer(text=f"Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
                await interaction.response.send_message(embed=embed, ephemeral=_ephemeral)
            except (KeyError, TypeError):
                embed = self.discord.Embed(
                    title="❌ Path Not Found",
                    description=f"Config path `{path}` does not exist",
                    color=0xE74C3C
                )
                await interaction.response.send_message(embed=embed, ephemeral=_ephemeral)

        @self.tree.command(name="pconfigget", description="[Private] [ADMIN] Get a configuration value")
        @self.app_commands.describe(path="Config path (e.g., general.prefix)")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def pconfigget_slash(interaction: self.discord.Interaction, path: str):
            await configget_slash(interaction, path, _ephemeral=True)

        # Config Set command
        @self.tree.command(name="configset", description="[ADMIN] Set a configuration value")
        @self.app_commands.describe(path="Config path (e.g., general.prefix)", value="New value")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def configset_slash(interaction: self.discord.Interaction, path: str, value: str, _ephemeral: bool = False):
            if not str(interaction.user.id) in self.config.admin_ids:
                await interaction.response.send_message("❌ This command is admin-only.", ephemeral=True)
                return
            
            if path in self.config.censor_config:
                await interaction.response.send_message(
                    f"❌ Cannot set a censored config key: `{path}`",
                    ephemeral=True
                )
                return
            
            await interaction.response.defer(ephemeral=_ephemeral)
            
            try:
                import toml
                keys, target = path.split("."), self.config.config_data
                for key in keys[:-1]:
                    target = target.setdefault(key, {})
                target[keys[-1]] = self.config.parse_value(value)
                with open(self.config.config_file, "w", encoding="utf-8") as f:
                    toml.dump(self.config.config_data, f)
                
                embed = self.discord.Embed(
                    title="✅ Config Updated",
                    description=f"Set `{path}` to `{target[keys[-1]]}`",
                    color=0x2ECC71,
                    timestamp=datetime.now()
                )
                embed.set_footer(text=f"Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
                await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
            except Exception as e:
                embed = self.discord.Embed(
                    title="❌ Update Failed",
                    description=f"Error: {str(e)}",
                    color=0xE74C3C
                )
                await interaction.followup.send(embed=embed, ephemeral=_ephemeral)

        @self.tree.command(name="pconfigset", description="[Private] [ADMIN] Set a configuration value")
        @self.app_commands.describe(path="Config path (e.g., general.prefix)", value="New value")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def pconfigset_slash(interaction: self.discord.Interaction, path: str, value: str):
            await configset_slash(interaction, path, value, _ephemeral=True)

        # Config Debug command
        @self.tree.command(name="configdebug", description="[ADMIN] Show debug configuration info")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def configdebug_slash(interaction: self.discord.Interaction, _ephemeral: bool = False):
            if not str(interaction.user.id) in self.config.admin_ids:
                await interaction.response.send_message("❌ This command is admin-only.", ephemeral=True)
                return
            
            gid = interaction.guild.id if interaction.guild else None
            uid = interaction.user.id
            cid = interaction.channel.id
            
            embed = self.discord.Embed(
                title=f"🔍 Debug Info for {self.client.user}",
                color=0x95A5A6,
                timestamp=datetime.now()
            )
            
            embed.add_field(name="👤 Is Admin", value=f"`{str(uid) in self.config.admin_ids}`", inline=True)
            embed.add_field(name="⚙️ Prefix", value=f"`{self.config.get_prefix(gid)}`", inline=True)
            embed.add_field(name="📝 Message Log", value=f"`{self.config.get_guild_config(gid, 'message-log', self.config.default_message_log, uid, cid)}`", inline=True)
            embed.add_field(name="📎 Attachment Log", value=f"`{self.config.get_attachment_log_setting(gid, uid, cid)}`", inline=True)
            embed.add_field(name="🚫 Prevent Deleting", value=f"`{self.config.get_guild_config(gid, 'prevent-deleting', self.config.default_prevent_deleting, uid, cid)}`", inline=True)
            embed.add_field(name="✏️ Prevent Editing", value=f"`{self.config.get_guild_config(gid, 'prevent-editing', self.config.default_prevent_editing, uid, cid)}`", inline=True)
            embed.add_field(name="💬 Allow Swears", value=f"`{self.config.get_guild_config(gid, 'allow-swears', self.config.default_allow_swears, uid, cid)}`", inline=True)
            embed.add_field(name="🔞 Allow Slurs", value=f"`{self.config.get_guild_config(gid, 'allow-slurs', self.config.default_allow_slurs, uid, cid)}`", inline=True)
            embed.add_field(name="🌐 Detect IPs", value=f"`{self.config.get_guild_config(gid, 'detect-ips', self.config.default_detect_ips, uid, cid)}`", inline=True)
            embed.add_field(name="🧹 Clean Spigey", value=f"`{self.config.default_clean_spigey}`", inline=True)
            embed.add_field(name="🎭 Match Status", value=f"`{self.config.match_status}`", inline=True)
            
            embed.set_footer(text=f"Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
            
            await interaction.response.send_message(embed=embed, ephemeral=_ephemeral)

        @self.tree.command(name="pconfigdebug", description="[Private] [ADMIN] Show debug configuration info")
        @self.app_commands.allowed_installs(guilds=True, users=True)
        @self.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        async def pconfigdebug_slash(interaction: self.discord.Interaction):
            await configdebug_slash(interaction, _ephemeral=True)

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