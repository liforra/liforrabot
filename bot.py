"""Main Bot class with event handlers."""

import asyncio
import re
import json
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, Union, Any
from utils.colored_logger import setup_logger, logger as log

# Setup logging
logger = setup_logger('liforrabot')

from datetime import datetime, timedelta, timezone
from collections import defaultdict

# It's good practice to handle both discord.py and selfcord imports gracefully
try:
    import discord
    from discord import app_commands
except ImportError:
    import selfcord as discord
    app_commands = None


from config.config_manager import ConfigManager
from handlers.alts_handler import AltsHandler
from handlers.ip_handler import IPHandler
from handlers.logging_handler import LoggingHandler
from handlers.oauth_handler import OAuthHandler
from handlers.phone_handler import PhoneHandler
from handlers.word_stats_handler import WordStatsHandler
from handlers.mc_server_handler import MCServerHandler
from commands.user_commands import UserCommands
from commands.admin_commands import AdminCommands
from utils.constants import SWEAR_WORDS, SLUR_WORDS, ASTEROIDE_BOT_ID
from utils.helpers import (
    split_message,
    calculate_edit_percentage,
    is_likely_typo,
)
from utils.steam_location_handler import SteamLocationHandler


class PaginationView:
    """Pagination view with buttons for navigating pages."""
    
    def __init__(self, embeds: List, discord_module, timeout: int = 180):
        self.discord = discord_module
        self.embeds = embeds
        self.current_page = 0
        self.message = None
        self.timeout = timeout
        self.view = self.discord.ui.View(timeout=self.timeout)
        self._create_view()
    
    def _create_view(self):
        """Creates the discord.ui.View with buttons."""
        
        first_btn = self.discord.ui.Button(label="⏮️", style=self.discord.ButtonStyle.gray)
        first_btn.callback = self._go_to_page_callback(0)
        self.view.add_item(first_btn)
        
        prev_btn = self.discord.ui.Button(label="◀️", style=self.discord.ButtonStyle.primary)
        prev_btn.callback = self._go_to_page_callback(self.current_page - 1)
        self.view.add_item(prev_btn)
        
        next_btn = self.discord.ui.Button(label="▶️", style=self.discord.ButtonStyle.primary)
        next_btn.callback = self._go_to_page_callback(self.current_page + 1)
        self.view.add_item(next_btn)
        
        last_btn = self.discord.ui.Button(label="⏭️", style=self.discord.ButtonStyle.gray)
        last_btn.callback = self._go_to_page_callback(len(self.embeds) - 1)
        self.view.add_item(last_btn)
        
        delete_btn = self.discord.ui.Button(label="🗑️", style=self.discord.ButtonStyle.danger)
        delete_btn.callback = self._delete_callback
        self.view.add_item(delete_btn)
        
        self._update_buttons()
    
    def _update_buttons(self):
        """Updates button states and callbacks based on current page."""
        self.view.children[0].callback = self._go_to_page_callback(0)
        self.view.children[1].callback = self._go_to_page_callback(self.current_page - 1)
        self.view.children[2].callback = self._go_to_page_callback(self.current_page + 1)
        self.view.children[3].callback = self._go_to_page_callback(len(self.embeds) - 1)

        self.view.children[0].disabled = self.current_page == 0
        self.view.children[1].disabled = self.current_page == 0
        self.view.children[2].disabled = self.current_page >= len(self.embeds) - 1
        self.view.children[3].disabled = self.current_page >= len(self.embeds) - 1

    def _go_to_page_callback(self, page_number: int):
        async def callback(interaction: discord.Interaction):
            self.current_page = max(0, min(page_number, len(self.embeds) - 1))
            self._update_buttons()
            await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self.view)
        return callback
    
    async def _delete_callback(self, interaction: discord.Interaction):
        await interaction.message.delete()


# =================================================================================
# SLASH COMMAND REGISTRATION
# =================================================================================

LANGUAGE_MAP = {
    "Germany": {"location": "Hamburg, Germany", "hl": "de", "gl": "de", "google_domain": "google.de"},
    "United States": {"location": "Austin, Texas, United States", "hl": "en", "gl": "us", "google_domain": "google.com"},
    "United Kingdom": {"location": "London, England, United Kingdom", "hl": "en", "gl": "uk", "google_domain": "google.co.uk"},
}

def register_slash_commands(tree, bot: "Bot"):
    """Registers all slash commands for the bot."""
    
    import httpx
    from utils.helpers import format_alt_name, is_valid_ip
    from utils.constants import COUNTRY_FLAGS

    # ==================== USER COMMANDS ====================
    
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @tree.command(name="trump", description="Get a random Trump quote")
    @bot.app_commands.describe(_ephemeral="Show the response only to you (default: False)")
    async def trump_slash(interaction: discord.Interaction, _ephemeral: bool = False):
        bot.log_command(interaction.user.id, str(interaction.user), "trump", [], is_slash=True)
        if not await bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(bot.oauth_handler.get_authorization_message(interaction.user.mention), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=_ephemeral)
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get("https://api.whatdoestrumpthink.com/api/v1/quotes/random", timeout=10)
                r.raise_for_status()
                quote = r.json().get("message", "Could not retrieve a quote.")
            embed = discord.Embed(description=f'*"{quote}"*', color=0xB32E2E)
            embed.set_author(name="Donald Trump", icon_url="https://i.imgur.com/GkZasg8.png")
            embed.set_footer(text="liforra.de | Liforras Utility bot")
            await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {type(e).__name__}", ephemeral=_ephemeral)

    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @tree.command(name="tech", description="Get a random tech tip or fact")
    @bot.app_commands.describe(_ephemeral="Show the response only to you (default: False)")
    async def tech_slash(interaction: discord.Interaction, _ephemeral: bool = False):
        bot.log_command(interaction.user.id, str(interaction.user), "tech", [], is_slash=True)
        if not await bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(bot.oauth_handler.get_authorization_message(interaction.user.mention), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=_ephemeral)
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get("https://techy-api.vercel.app/api/json", timeout=10)
                r.raise_for_status()
                message = r.json().get("message", "Could not retrieve a tech tip.")
            embed = discord.Embed(title="💡 Tech Tip", description=message, color=0x00D4AA)
            embed.set_thumbnail(url="https://i.imgur.com/3Q3Q1aD.png")
            embed.set_footer(text="liforra.de | Liforras Utility bot | Powered by Techy API")
            await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {type(e).__name__}", ephemeral=_ephemeral)

    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @tree.command(name="fact", description="Get a random or daily useless fact")
    @bot.app_commands.describe(
        fact_type="Type of fact", 
        language="Language (en or de)",
        _ephemeral="Show the response only to you (default: False)"
    )
    @bot.app_commands.choices(fact_type=[
        bot.app_commands.Choice(name="Random", value="random"),
        bot.app_commands.Choice(name="Today's Fact", value="today")
    ], language=[
        bot.app_commands.Choice(name="English", value="en"),
        bot.app_commands.Choice(name="German", value="de")
    ])
    async def fact_slash(interaction: discord.Interaction, fact_type: str = "random", language: str = "en", _ephemeral: bool = False):
        bot.log_command(interaction.user.id, str(interaction.user), "fact", [fact_type, language], is_slash=True)
        if not await bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(bot.oauth_handler.get_authorization_message(interaction.user.mention), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=_ephemeral)
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"https://uselessfacts.jsph.pl/api/v2/facts/{fact_type}", params={"language": language}, timeout=10)
                r.raise_for_status()
                data = r.json()

            if data.get("error"):
                await interaction.followup.send(f"❌ API Error: {data['error']}", ephemeral=_ephemeral)
                return

            embeds, organic_results = [], data.get("organic_results", [])
            summary_embed = discord.Embed(title="🔍 Google Search", description=f"**Query:** `{query}`", color=0x4285F4, url=data.get("search_metadata", {}).get("google_url"))
            summary_embed.set_thumbnail(url="https://i.imgur.com/tEChjwx.png")

            if (info := data.get("search_information")) and (total_results := info.get("total_results")):
                summary_embed.add_field(name="📊 Stats", value=f"{total_results:,} results\n({info.get('time_taken_displayed', 'N/A')}s)", inline=True)
            summary_embed.add_field(name="📍 Region", value=_language, inline=True)

            if (answer_box := data.get("answer_box")) and (answer := answer_box.get("answer")):
                summary_embed.add_field(name="💡 Quick Answer", value=answer[:1000] + ("..." if len(answer) > 1000 else ""), inline=False)
            elif (kg := data.get("knowledge_graph")) and (kg_title := kg.get("title")):
                kg_text = f"**{kg_title}**" + (f" _{kg_type}_" if (kg_type := kg.get("type")) else "") + (f"\n{kg_desc[:200] + ('...' if len(kg_desc) > 200 else '')}" if (kg_desc := kg.get("description")) else "")
                summary_embed.add_field(name="📚 Knowledge Graph", value=kg_text, inline=False)
            
            if organic_results:
                top_hit = organic_results[0]
                value = f"**[{top_hit.get('title', 'No Title')}]({top_hit.get('link', '#')})**\n_{top_hit.get('snippet', 'No snippet available.')[:150] + ('...' if len(top_hit.get('snippet', 'No snippet available.')) > 150 else '')}_"
                summary_embed.add_field(name="🏆 Top Result", value=value, inline=False)
            embeds.append(summary_embed)

            if organic_results:
                for i in range(0, len(organic_results), 3):
                    chunk, page_num = organic_results[i : i + 3], (i // 3) + 1
                    page_embed = discord.Embed(title=f"Search Results (Page {page_num})", color=0x34A853)
                    for result in chunk:
                        value_text = f"_{result.get('snippet', 'No snippet available.')[:200] + ('...' if len(result.get('snippet', 'No snippet available.')) > 200 else '')}_\n**[Read More]({result.get('link', '#')})**"
                        page_embed.add_field(name=f"📄 {result.get('title', 'No Title')}", value=value_text, inline=False)
                    embeds.append(page_embed)

            if not any(embed.fields or embed.description for embed in embeds):
                await interaction.followup.send("❌ No results found.", ephemeral=_ephemeral)
                return

            for i, embed in enumerate(embeds):
                embed.set_footer(text=f"liforra.de | Liforras Utility bot | Powered by SerpAPI | Page {i+1}/{len(embeds)}")
            
            view = PaginationView(embeds, bot.discord) if len(embeds) > 1 else None
            await interaction.followup.send(embed=embeds[0], view=view, ephemeral=_ephemeral)

        except httpx.HTTPStatusError as e:
            await interaction.followup.send(f"❌ API Error: {e.response.status_code}", ephemeral=_ephemeral)
        except Exception as e:
            await interaction.followup.send(f"❌ An unexpected error occurred: {type(e).__name__}", ephemeral=_ephemeral)

    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @tree.command(name="stats", description="Display word usage statistics")
    @bot.app_commands.describe(
        mode="Statistic to display",
        target_user="User to inspect (required for user mode)",
        word="Word to inspect (required for word mode)",
        limit="Number of entries to show (1-25)",
        guild_only="Restrict results to the current server",
        _ephemeral="Show the response only to you (default: False)",
    )
    @bot.app_commands.choices(
        mode=[
            bot.app_commands.Choice(name="Global top words", value="overall"),
            bot.app_commands.Choice(name="Guild top words", value="guild"),
            bot.app_commands.Choice(name="Most used word", value="most"),
            bot.app_commands.Choice(name="User's top words", value="user"),
            bot.app_commands.Choice(name="Word usage", value="word"),
        ]
    )
    async def stats_slash(
        interaction: discord.Interaction,
        mode: str,
        target_user: Optional[discord.User] = None,
        word: Optional[str] = None,
        limit: int = 10,
        guild_only: bool = False,
        _ephemeral: bool = False,
    ):
        handler = bot.word_stats_handler
        if not handler or not handler.available:
            await interaction.response.send_message("❌ Word statistics database is not configured.", ephemeral=True)
            return

        limit = max(1, min(25, limit))
        await interaction.response.defer(ephemeral=_ephemeral, thinking=True)

        entries = []
        title = ""

        if mode == "overall":
            entries = await handler.get_global_top_words(limit)
            title = f"Top {len(entries)} Words (Global)"
        elif mode == "guild":
            if not interaction.guild:
                await interaction.followup.send("❌ This mode can only be used in a server.", ephemeral=_ephemeral)
                return
            entries = await handler.get_guild_top_words(interaction.guild.id, limit)
            title = f"Top {len(entries)} Words in {interaction.guild.name}"
        elif mode == "most":
            entries = await handler.get_global_top_words(1)
            title = "Most Used Word (Global)"
        elif mode == "user":
            if not target_user:
                await interaction.followup.send("❌ Please specify a user.", ephemeral=_ephemeral)
                return
            display_name = getattr(target_user, "display_name", target_user.name)
            if guild_only:
                if not interaction.guild:
                    await interaction.followup.send("❌ Server-specific stats require running this command in a server.", ephemeral=_ephemeral)
                    return
                entries = await handler.get_user_guild_top_words(interaction.guild.id, target_user.id, limit)
                title = f"Top {len(entries)} Words for {display_name} in {interaction.guild.name}"
            else:
                entries = await handler.get_user_top_words(target_user.id, limit)
                title = f"Top {len(entries)} Words for {display_name} (Global)"
        elif mode == "word":
            if not word:
                await interaction.followup.send("❌ Please provide a word to inspect.", ephemeral=_ephemeral)
                return
            word_value = word.lower()
            if guild_only:
                if not interaction.guild:
                    await interaction.followup.send("❌ Server-specific stats require running this command in a server.", ephemeral=_ephemeral)
                    return
                guild_id = interaction.guild.id
            else:
                guild_id = None
            entries = await handler.get_word_usage_per_user(word_value, limit, guild_id)
            title = f"Usage of `{word_value}`{' in ' + interaction.guild.name if guild_id else ''}"
        else:
            await interaction.followup.send("❌ Unknown mode.", ephemeral=_ephemeral)
            return

        if not entries:
            await interaction.followup.send(f"❌ No statistics available for {title or mode}.", ephemeral=_ephemeral)
            return

        lines = [f"**{title}:**"]

        if mode == "word":
            guild_limited = guild_only and interaction.guild
            for idx, row in enumerate(entries, start=1):
                user_display = f"<@{row['user_id']}>"
                if interaction.guild:
                    member = interaction.guild.get_member(row['user_id'])
                    if member:
                        user_display = member.display_name
                if not guild_limited:
                    gid = row.get("guild_id")
                    if not gid:
                        guild_info = " (DMs)"
                    else:
                        guild = bot.client.get_guild(gid)
                        guild_info = f" ({guild.name})" if guild else f" ({gid})"
                else:
                    guild_info = ""
                lines.append(f"{idx}. {user_display} — {row['count']:,}{guild_info}")
        else:
            for idx, item in enumerate(entries, start=1):
                lines.append(f"{idx}. `{item['word']}` — {item['count']:,}")

        lines.append("\n*liforra.de | Liforras Utility bot*")
        await interaction.followup.send("\n".join(lines), ephemeral=_ephemeral)

    @bot.app_commands.allowed_installs(guilds=True, users=False)
    @bot.app_commands.allowed_contexts(guilds=True, dms=False, private_channels=True)
    @tree.command(name="backfill", description="Backfill word statistics for this channel")
    @bot.app_commands.describe(
        days="Number of days to backfill (1-30, default 7)",
        _ephemeral="Show the response only to you (default: False)",
    )
    async def backfill_slash(
        interaction: discord.Interaction,
        days: int = 7,
        _ephemeral: bool = False,
    ):
        handler = bot.word_stats_handler
        if not handler or not handler.available:
            await interaction.response.send_message("❌ Word statistics database is not configured.", ephemeral=True)
            return

        if str(interaction.user.id) not in bot.config.admin_ids:
            await interaction.response.send_message("❌ Only bot admins can run backfill.", ephemeral=True)
            return

        if not interaction.guild or not hasattr(interaction.channel, "history"):
            await interaction.response.send_message("❌ Backfill can only be used in regular server channels.", ephemeral=True)
            return

        span_text = ""
        cutoff = None
        if days is None or days <= 0:
            span_text = "all available history"
        else:
            span_text = f"the last {days} day(s)"
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        await interaction.response.defer(ephemeral=_ephemeral, thinking=True)

        processed = 0

        try:
            async for message in interaction.channel.history(limit=None, after=cutoff, oldest_first=True):
                if message.author.bot or (bot.client and message.author.id == bot.client.user.id):
                    continue
                await handler.record_message(interaction.guild.id, message.author.id, message.content)
                processed += 1
                if processed % 200 == 0:
                    await asyncio.sleep(0)
        except Exception as e:
            tb_str = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
            await interaction.followup.send(f"❌ **Backfill failed:**\n```py\n{tb_str[:1800]}\n```", ephemeral=_ephemeral)
            return

        await interaction.followup.send(
            f"✅ Backfill complete. Processed {processed} messages from {span_text or 'all available history'}.",
            ephemeral=_ephemeral,
        )

    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @tree.command(name="websites", description="Check status of configured websites")
    @bot.app_commands.describe(_ephemeral="Show the response only to you (default: False)")
    async def websites_slash(interaction: discord.Interaction, _ephemeral: bool = False):
        bot.log_command(interaction.user.id, str(interaction.user), "websites", [], is_slash=True)
        if not await bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(bot.oauth_handler.get_authorization_message(interaction.user.mention), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=_ephemeral)
        
        gid = interaction.guild.id if interaction.guild else None
        sites = bot.config.get_guild_config(gid, "websites", [], interaction.user.id, interaction.channel.id)
        friend_sites = bot.config.get_guild_config(gid, "friend_websites", [], interaction.user.id, interaction.channel.id)
        
        embed = discord.Embed(title="🌐 Website Status", color=0x3498DB, timestamp=datetime.now())
        
        async with httpx.AsyncClient() as client:
            if sites:
                responses = await asyncio.gather(*[client.head(s, timeout=10) for s in sites], return_exceptions=True)
                embed.add_field(name="Main Websites", value="\n".join([f"🟢 `{s}` ({r.status_code})" if isinstance(r, httpx.Response) and 200 <= r.status_code < 400 else f"🔴 `{s}` ({type(r).__name__ if isinstance(r, Exception) else r.status_code})" for s, r in zip(sites, responses)]), inline=False)
            if friend_sites:
                responses = await asyncio.gather(*[client.head(s, timeout=10) for s in friend_sites], return_exceptions=True)
                embed.add_field(name="Friends' Websites", value="\n".join([f"🟢 `{s}` ({r.status_code})" if isinstance(r, httpx.Response) and 200 <= r.status_code < 400 else f"🔴 `{s}` ({type(r).__name__ if isinstance(r, Exception) else r.status_code})" for s, r in zip(friend_sites, responses)]), inline=False)
        
        if not embed.fields: 
            embed.description = "No websites configured."
        embed.set_footer(text="liforra.de | Liforras Utility bot")
        await interaction.followup.send(embed=embed, ephemeral=_ephemeral)

    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @tree.command(name="pings", description="Ping configured devices")
    @bot.app_commands.describe(_ephemeral="Show the response only to you (default: False)")
    async def pings_slash(interaction: discord.Interaction, _ephemeral: bool = False):
        bot.log_command(interaction.user.id, str(interaction.user), "pings", [], is_slash=True)
        if not await bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(bot.oauth_handler.get_authorization_message(interaction.user.mention), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=_ephemeral)
        
        async def _ping(h):
            p = await asyncio.create_subprocess_exec("ping", "-c", "1", "-W", "1", h, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await p.wait()
            return (h.replace('.liforra.de', ''), "🟢 Online" if p.returncode == 0 else "🔴 Offline", p.returncode == 0)

        devices = ["alhena.liforra.de", "sirius.liforra.de", "chaosserver.liforra.de", "antares.liforra.de"]
        results = await asyncio.gather(*[_ping(dev) for dev in devices])
        
        embed = discord.Embed(title="🖥️ Device Status", color=0x2ECC71, timestamp=datetime.now())
        for name, status, _ in results:
            embed.add_field(name=name, value=status, inline=True)
        
        online_count = sum(1 for _, _, is_online in results if is_online)
        embed.set_footer(text=f"liforra.de | Liforras Utility bot | {online_count}/{len(results)} devices online")
        await interaction.followup.send(embed=embed, ephemeral=_ephemeral)

    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @tree.command(name="ip", description="Get information about an IP address")
    @bot.app_commands.describe(address="The IP address to look up (IPv4 or IPv6)", _ephemeral="Show the response only to you (default: False)")
    async def ip_slash(interaction: discord.Interaction, address: str, _ephemeral: bool = False):
        bot.log_command(interaction.user.id, str(interaction.user), "ip", [address], is_slash=True)
        if not await bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(bot.oauth_handler.get_authorization_message(interaction.user.mention), ephemeral=True)
            return
        
        is_allowed, wait_time = bot.check_rate_limit(interaction.user.id, "ip", limit=10, window=60)
        if not is_allowed:
            await interaction.response.send_message(f"⏱️ Rate limit exceeded. Please wait {wait_time} seconds.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=_ephemeral)
        
        if not is_valid_ip(address):
            await interaction.followup.send("❌ Invalid IP address format.", ephemeral=_ephemeral)
            return
        
        ip_data = await bot.ip_handler.fetch_ip_info(address)
        if not ip_data:
            await interaction.followup.send(f"❌ Failed to fetch info for `{address}`.", ephemeral=_ephemeral)
            return
        
        flag = COUNTRY_FLAGS.get(ip_data.get("countryCode", ""), "🌐")
        embed = discord.Embed(title=f"{flag} IP Information", description=f"**IP Address:** `{address}`", color=0x3498DB, timestamp=datetime.now())
        from utils.helpers import is_valid_ipv6
        if not is_valid_ipv6(address):
            embed.url = f"https://whatismyipaddress.com/ip/{address}"
        
        loc = ", ".join(filter(None, [ip_data.get('city'), ip_data.get('regionName'), f"{ip_data.get('country')} ({ip_data.get('countryCode')})"]))
        if loc: 
            embed.add_field(name="📍 Location", value=loc, inline=False)
        
        net = "\n".join(filter(None, [f"**ISP:** {ip_data.get('isp')}" if ip_data.get('isp') else None, f"**AS:** {ip_data.get('as')}" if ip_data.get('as') else None]))
        if net: 
            embed.add_field(name="🌐 Network", value=net, inline=False)
        
        sec_flags = []
        if vpn := bot.ip_handler.detect_vpn_provider(ip_data.get("isp", ""), ip_data.get("org", "")): 
            sec_flags.append(f"🔒 **VPN Provider:** {vpn}")
        elif ip_data.get("proxy"): 
            sec_flags.append("🔒 **Proxy/VPN Detected**")
        if ip_data.get("hosting"): 
            sec_flags.append("☁️ **Hosting Service**")
        if sec_flags: 
            embed.add_field(name="🛡️ Security", value="\n".join(sec_flags), inline=False)
        
        embed.set_footer(text="liforra.de | Liforras Utility bot | Powered by ip-api.com")
        await interaction.followup.send(embed=embed, ephemeral=_ephemeral)

    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @tree.command(name="ipdbinfo", description="Get cached information about an IP from database")
    @bot.app_commands.describe(address="The IP address to look up", _ephemeral="Show the response only to you (default: False)")
    async def ipdbinfo_slash(interaction: discord.Interaction, address: str, _ephemeral: bool = False):
        bot.log_command(interaction.user.id, str(interaction.user), "ipdbinfo", [address], is_slash=True)
        if not await bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(bot.oauth_handler.get_authorization_message(interaction.user.mention), ephemeral=True)
            return
        
        if address not in bot.ip_handler.ip_geo_data:
            await interaction.response.send_message(f"❌ No data for `{address}` in database.", ephemeral=_ephemeral)
            return

        geo = bot.ip_handler.ip_geo_data[address]
        flag = COUNTRY_FLAGS.get(geo.get("countryCode", ""), "🌐")
        embed = discord.Embed(title=f"{flag} Cached IP Information", description=f"**IP Address:** `{address}`", color=0x9B59B6)
        
        ts = int(datetime.fromisoformat(geo.get("last_updated")).timestamp())
        embed.add_field(name="Last Updated", value=f"<t:{ts}:R>")
        loc = ", ".join(filter(None, [geo.get('city'), geo.get('regionName'), f"{geo.get('country')} ({geo.get('countryCode')})"]))
        if loc: 
            embed.add_field(name="📍 Location", value=loc, inline=False)
        net = "\n".join(filter(None, [f"**ISP:** {geo.get('isp')}" if geo.get('isp') else None, f"**Org:** {geo.get('org')}" if geo.get('org') else None]))
        if net: 
            embed.add_field(name="🌐 Network", value=net, inline=False)
        
        embed.set_footer(text="liforra.de | Liforras Utility bot")
        await interaction.response.send_message(embed=embed, ephemeral=_ephemeral)

    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @tree.command(name="ipdblist", description="List all cached IPs in database")
    @bot.app_commands.describe(page="Page number (default: 1)", _ephemeral="Show the response only to you (default: False)")
    async def ipdblist_slash(interaction: discord.Interaction, page: int = 1, _ephemeral: bool = False):
        bot.log_command(interaction.user.id, str(interaction.user), "ipdblist", [str(page)], is_slash=True)
        if not await bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(bot.oauth_handler.get_authorization_message(interaction.user.mention), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=_ephemeral)
        
        ips = sorted(bot.ip_handler.ip_geo_data.keys())
        if not ips:
            await interaction.followup.send("❌ No IPs in database.", ephemeral=_ephemeral)
            return
        
        per_page = 15
        total_pages = (len(ips) + per_page - 1) // per_page
        embeds = []
        for page_num in range(1, total_pages + 1):
            start = (page_num - 1) * per_page
            embed = discord.Embed(title="🗄️ IP Database", description=f"Showing cached IP addresses", color=0x9B59B6)
            embed.add_field(name=f"IP Addresses ({len(ips)} total)", value="\n".join([bot.ip_handler.format_ip_with_geo(ip) for ip in ips[start:start+per_page]]), inline=False)
            embed.set_footer(text=f"liforra.de | Liforras Utility bot | Page {page_num}/{total_pages}")
            embeds.append(embed)
        
        view = PaginationView(embeds, bot.discord) if len(embeds) > 1 else None
        page_to_show = page - 1 if 0 <= page - 1 < len(embeds) else 0
        if view: 
            view.current_page = page_to_show
        await interaction.followup.send(embed=embeds[page_to_show], view=view, ephemeral=_ephemeral)

    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @tree.command(name="ipdbsearch", description="Search IPs by country, city, or ISP")
    @bot.app_commands.describe(term="Search term", _ephemeral="Show the response only to you (default: False)")
    async def ipdbsearch_slash(interaction: discord.Interaction, term: str, _ephemeral: bool = False):
        bot.log_command(interaction.user.id, str(interaction.user), "ipdbsearch", [term], is_slash=True)
        if not await bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(bot.oauth_handler.get_authorization_message(interaction.user.mention), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=_ephemeral)
        
        search_term = term.lower()
        results = [ip for ip, geo in bot.ip_handler.ip_geo_data.items() if search_term in " ".join(filter(None, [geo.get(k) for k in ["country", "regionName", "city", "isp", "org"]])).lower()]

        if not results:
            await interaction.followup.send(f"❌ No IPs found matching '{term}'.", ephemeral=_ephemeral)
            return

        per_page = 15
        total_pages = (len(results) + per_page - 1) // per_page
        embeds = []
        for page_num in range(1, total_pages + 1):
            start = (page_num - 1) * per_page
            embed = discord.Embed(title=f"🔍 Search Results for '{term}'", description=f"Found {len(results)} matching IP(s)", color=0xE67E22)
            embed.add_field(name="Matching IPs", value="\n".join([bot.ip_handler.format_ip_with_geo(ip) for ip in results[start:start+per_page]]), inline=False)
            embed.set_footer(text=f"liforra.de | Liforras Utility bot | Page {page_num}/{total_pages}")
            embeds.append(embed)

        view = PaginationView(embeds, bot.discord) if len(embeds) > 1 else None
        await interaction.followup.send(embed=embeds[0], view=view, ephemeral=_ephemeral)

    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @tree.command(name="ipdbstats", description="Show IP database statistics")
    @bot.app_commands.describe(_ephemeral="Show the response only to you (default: False)")
    async def ipdbstats_slash(interaction: discord.Interaction, _ephemeral: bool = False):
        bot.log_command(interaction.user.id, str(interaction.user), "ipdbstats", [], is_slash=True)
        if not await bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(bot.oauth_handler.get_authorization_message(interaction.user.mention), ephemeral=True)
            return
        
        total_ips = len(bot.ip_handler.ip_geo_data)
        countries = {geo.get("countryCode") for geo in bot.ip_handler.ip_geo_data.values() if geo.get("countryCode")}
        vpn_count = sum(1 for geo in bot.ip_handler.ip_geo_data.values() if bot.ip_handler.detect_vpn_provider(geo.get("isp", ""), geo.get("org", "")) or geo.get("proxy"))
        hosting_count = sum(1 for geo in bot.ip_handler.ip_geo_data.values() if geo.get("hosting"))

        embed = discord.Embed(title="📊 IP Database Statistics", color=0x1ABC9C, timestamp=datetime.now())
        embed.add_field(name="Total IPs", value=f"**{total_ips}**", inline=True)
        embed.add_field(name="Unique Countries", value=f"**{len(countries)}**", inline=True)
        embed.add_field(name="VPN/Proxy", value=f"**{vpn_count}**", inline=True)
        embed.add_field(name="VPS/Hosting", value=f"**{hosting_count}**", inline=True)
        embed.set_footer(text="liforra.de | Liforras Utility bot")
        await interaction.response.send_message(embed=embed, ephemeral=_ephemeral)

    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @tree.command(name="playerinfo", description="Get detailed information about a player")
    @bot.app_commands.describe(
        username="The username or ID to look up",
        account_type="The platform/account type",
        _ephemeral="Show the response only to you (default: False)"
    )
    @bot.app_commands.choices(account_type=[
        bot.app_commands.Choice(name="Minecraft", value="minecraft"),
        bot.app_commands.Choice(name="Steam", value="steam"),
        bot.app_commands.Choice(name="Xbox", value="xbox")
    ])
    async def playerinfo_slash(
        interaction: discord.Interaction, 
        username: str, 
        account_type: str = "minecraft",
        _ephemeral: bool = False
    ):
        bot.log_command(interaction.user.id, str(interaction.user), "playerinfo", [username, account_type], is_slash=True)
        if not await bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(bot.oauth_handler.get_authorization_message(interaction.user.mention), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=_ephemeral)
        
        account_type = account_type.lower()
        
        try:
            if account_type == "steam" and not username.isdigit():
                if resolved_id := await bot.user_commands_handler._resolve_steam_vanity_url(username):
                    username = resolved_id
            
            url = f"https://playerdb.co/api/player/{account_type}/{username}"
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    headers={"User-Agent": "https://liforra.de"},
                    timeout=10
                )
                response.raise_for_status()
                data = response.json()
                
                if data.get("code") != "player.found":
                    return await interaction.followup.send(
                        f"❌ {account_type.capitalize()} account `{username}` not found",
                        ephemeral=_ephemeral
                    )
                
                player = data["data"]["player"]
                
                if account_type == "minecraft":
                    embed = bot.user_commands_handler._format_minecraft_info(player, bot.discord)
                elif account_type == "steam":
                    embed = bot.user_commands_handler._format_steam_info(player, bot.discord)
                elif account_type == "xbox":
                    embed = bot.user_commands_handler._format_xbox_info(player, bot.discord)

                if embed:
                    await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
                else: 
                    await interaction.followup.send("❌ Failed to generate player info embed.", ephemeral=_ephemeral)

        except httpx.HTTPStatusError as e:
            if account_type == "xbox" and 500 <= e.response.status_code < 600:
                await interaction.followup.send(f"❌ The Xbox lookup API returned an error ({e.response.status_code}). It might be temporarily down.", ephemeral=_ephemeral)
            else:
                await interaction.followup.send(f"❌ API Error: {e.response.status_code}", ephemeral=_ephemeral)
        except Exception as e:
            tb_str = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
            error_message = f"❌ **An unexpected error occurred:**\n```py\n{tb_str[:1800]}\n```"
            await interaction.followup.send(error_message, ephemeral=_ephemeral)

    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @tree.command(name="namehistory", description="Get complete Minecraft name change history")
    @bot.app_commands.describe(username="The Minecraft username to look up", _ephemeral="Show the response only to you (default: False)")
    async def namehistory_slash(interaction: discord.Interaction, username: str, _ephemeral: bool = False):
        bot.log_command(interaction.user.id, str(interaction.user), "namehistory", [username], is_slash=True)
        if not await bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(bot.oauth_handler.get_authorization_message(interaction.user.mention), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=_ephemeral)
        
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"https://liforra.de/api/namehistory?username={username}", timeout=15)
                r.raise_for_status()
                data = r.json()
            
            if not data.get("history"):
                await interaction.followup.send(f"❌ No name history found for `{discord.utils.escape_markdown(username)}`.", ephemeral=_ephemeral)
                return
            
            safe_username = discord.utils.escape_markdown(username)
            embed = discord.Embed(title=f"📜 Name History for {safe_username}", url=f"https://namemc.com/profile/{username}", color=0x9B59B6)
            if uuid := data.get("uuid"): 
                embed.add_field(name="🆔 UUID", value=f"`{uuid}`", inline=False)
            
            if last_seen_str := data.get("last_seen_at"):
                try:
                    last_seen_dt = datetime.fromisoformat(last_seen_str.replace("Z", "+00:00"))
                    last_seen_ts = int(last_seen_dt.timestamp())
                    embed.add_field(name="👁️ Last Seen", value=f"<t:{last_seen_ts}:R>")
                except: pass
            
            history = sorted(data["history"], key=lambda x: x.get("id", 0))
            changes_text = []
            for entry in history:
                safe_name = discord.utils.escape_markdown(entry['name'])
                if ts_str := entry.get("changed_at"):
                    try:
                        ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        ts = int(ts_dt.timestamp())
                        changes_text.append(f"• **{safe_name}** - <t:{ts}:D>")
                    except:
                        changes_text.append(f"• **{safe_name}** - {ts_str[:10]}")
                else:
                    changes_text.append(f"• **{safe_name}** - Original Name")

            embed.description = "\n".join(changes_text)
            embed.set_footer(text="liforra.de | Liforras Utility bot | Powered by liforra.de API")
            await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
        except httpx.HTTPStatusError as e:
            await interaction.followup.send(f"❌ API Error: {e.response.status_code}", ephemeral=_ephemeral)
        except Exception as e:
            tb_str = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
            error_message = f"❌ **An unexpected error occurred:**\n```py\n{tb_str[:1800]}\n```"
            await interaction.followup.send(error_message, ephemeral=_ephemeral)

    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @tree.command(name="alts", description="Look up a user's known alts")
    @bot.app_commands.describe(
        username="The username to look up",
        _ip="[ADMIN] Show IP addresses (default: False)",
        _ephemeral="Show the response only to you (default: False)"
    )
    async def alts_slash(interaction: discord.Interaction, username: str, _ip: bool = False, _ephemeral: bool = False):
        if not await bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(bot.oauth_handler.get_authorization_message(interaction.user.mention), ephemeral=True)
            return
        
        is_allowed, wait_time = bot.check_rate_limit(interaction.user.id, "alts", limit=2, window=60)
        if not is_allowed:
            await interaction.response.send_message(f"⏱️ Rate limit exceeded. Please wait {wait_time} seconds.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=_ephemeral)
        bot.log_command(interaction.user.id, str(interaction.user), "alts", [username, str(_ip)], is_slash=True)
        
        search_term = username
        found_user = None
        lowercase_map = {k.lower(): k for k in bot.alts_handler.alts_data.keys()}
        for candidate in [search_term, f".{search_term}", f"...{search_term}"]:
            if candidate.lower() in lowercase_map:
                found_user = lowercase_map[candidate.lower()]
                break

        if not found_user:
            await interaction.followup.send(f"❌ No data for `{username}`.", ephemeral=_ephemeral)
            return

        data = bot.alts_handler.alts_data[found_user]
        alts = sorted(list(data.get("alts", set())))
        ips = sorted(list(data.get("ips", set())))
        
        is_admin = str(interaction.user.id) in bot.config.admin_ids
        show_ips = _ip and is_admin
        embeds = []
        
        def _safe_timestamp(ts_str):
            if not ts_str:
                return None
            try:
                return int(datetime.fromisoformat(ts_str).timestamp())
            except (ValueError, TypeError):
                try:
                    return int(datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp())
                except Exception:
                    return None

        first_seen_ts = _safe_timestamp(data.get("first_seen"))
        last_updated_ts = _safe_timestamp(data.get("last_updated"))

        desc_lines = []
        desc_lines.append(f"First Seen: <t:{first_seen_ts}:F>" if first_seen_ts is not None else "First Seen: Unknown")
        desc_lines.append(f"Last Updated: <t:{last_updated_ts}:R>" if last_updated_ts is not None else "Last Updated: Unknown")

        info_embed = discord.Embed(title=f"👥 Alt Report for {discord.utils.escape_markdown(found_user)}", color=0xE74C3C, description="\n".join(desc_lines))
        embeds.append(info_embed)

        if alts:
            for i in range(0, len(alts), 20):
                page = alts[i:i + 20]
                embed = discord.Embed(title=f"Known Alts ({len(alts)} total) - Page {(i//20)+1}", color=0xE74C3C)
                embed.description = "\n".join([format_alt_name(alt) for alt in page])
                embeds.append(embed)
        
        if show_ips and ips:
            for i in range(0, len(ips), 15):
                page = ips[i:i + 15]
                embed = discord.Embed(title=f"Known IPs ({len(ips)} total) - Page {(i//15)+1}", color=0xE74C3C)
                embed.description = "\n".join([bot.ip_handler.format_ip_with_geo(ip) for ip in page])
                embeds.append(embed)

        for i, embed in enumerate(embeds):
            embed.set_footer(text=f"liforra.de | Liforras Utility bot | Page {i+1}/{len(embeds)}")

        pagination = PaginationView(embeds, bot.discord) if len(embeds) > 1 else None
        sent_message = await interaction.followup.send(
            embed=embeds[0],
            view=pagination.view if pagination else None,
            ephemeral=_ephemeral,
        )

        if pagination:
            pagination.message = sent_message

    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @tree.command(name="phone", description="Look up phone number information")
    @bot.app_commands.describe(number="Phone number with country code (e.g., +4917674905246)", _ephemeral="Show the response only to you (default: False)")
    async def phone_slash(interaction: discord.Interaction, number: str, _ephemeral: bool = False):
        bot.log_command(interaction.user.id, str(interaction.user), "phone", [number], is_slash=True)
        if not await bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(bot.oauth_handler.get_authorization_message(interaction.user.mention), ephemeral=True)
            return
        
        is_allowed, wait_time = bot.check_rate_limit(interaction.user.id, "phone", limit=5, window=60)
        if not is_allowed:
            await interaction.response.send_message(f"⏱️ Rate limit exceeded. Please wait {wait_time} seconds.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=_ephemeral)
        
        if not bot.config.numlookup_api_key:
            await interaction.followup.send("❌ Phone lookup API key not configured.", ephemeral=_ephemeral)
            return
        
        number = '+' + number if not number.startswith('+') else number
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"https://api.numlookupapi.com/v1/validate/{number}", headers={"apikey": bot.config.numlookup_api_key}, timeout=10)
                response.raise_for_status()
                data = response.json()
                
                if not data.get("valid"):
                    await interaction.followup.send(f"❌ Invalid phone number: `{number}`", ephemeral=_ephemeral)
                    return
                
                # Store the lookup in database
                bot.phone_handler.store_phone_lookup(
                    discord_user_id=str(interaction.user.id),
                    phone_number=number,
                    lookup_data=data
                )
                
                flag = COUNTRY_FLAGS.get(data.get("country_code", ""), "🌐")
                embed = discord.Embed(title="📱 Phone Number Information", color=0x3498DB, timestamp=datetime.now())
                embed.add_field(name="Number", value=f"`{data.get('number', 'N/A')}`", inline=False)
                embed.add_field(name="Local Format", value=f"`{data.get('local_format', 'N/A')}`", inline=True)
                embed.add_field(name="International", value=f"`{data.get('international_format', 'N/A')}`", inline=True)
                embed.add_field(name=f"{flag} Country", value=f"{data.get('country_name', 'N/A')} ({data.get('country_code', 'N/A')})", inline=False)
                embed.add_field(name="📡 Carrier", value=data.get('carrier', 'N/A'), inline=True)
                embed.add_field(name="📞 Line Type", value=data.get('line_type', 'N/A').title(), inline=True)
                if location := data.get('location'):
                    embed.add_field(name="📍 Location", value=location, inline=False)
                embed.set_footer(text="liforra.de | Liforras Utility bot | Powered by NumLookupAPI")
                await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
                
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401: await interaction.followup.send("❌ Invalid NumLookupAPI key.", ephemeral=_ephemeral)
            elif e.response.status_code == 429: await interaction.followup.send("⏱️ API rate limit exceeded.", ephemeral=_ephemeral)
            else: await interaction.followup.send(f"❌ API Error: {e.response.status_code}", ephemeral=_ephemeral)
        except Exception as e:
            tb_str = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
            error_message = f"❌ **An unexpected error occurred:**\n```py\n{tb_str[:1800]}\n```"
            await interaction.followup.send(error_message, ephemeral=_ephemeral)

    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @tree.command(name="shodan", description="Get Shodan host information")
    @bot.app_commands.describe(ip="IP address to look up", _ephemeral="Show the response only to you (default: False)")
    async def shodan_slash(interaction: discord.Interaction, ip: str, _ephemeral: bool = False):
        bot.log_command(interaction.user.id, str(interaction.user), "shodan", [ip], is_slash=True)
        if not await bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(bot.oauth_handler.get_authorization_message(interaction.user.mention), ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=_ephemeral)
        
        if not bot.config.shodan_api_key:
            await interaction.followup.send("❌ Shodan API key not configured.", ephemeral=_ephemeral)
            return
        
        if not is_valid_ip(ip):
            await interaction.followup.send("❌ Invalid IP address format.", ephemeral=_ephemeral)
            return
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"https://api.shodan.io/shodan/host/{ip}", params={"key": bot.config.shodan_api_key}, timeout=20)
                response.raise_for_status()
                data = response.json()
                
                flag = COUNTRY_FLAGS.get(data.get("country_code", ""), "🌐")
                embed = discord.Embed(title=f"🔍 Shodan: {ip}", url=f"https://www.shodan.io/host/{ip}", color=0xE74C3C, timestamp=datetime.now())
                
                embed.add_field(name=f"{flag} Country", value=data.get('country_name', 'N/A'), inline=True)
                embed.add_field(name="Organization", value=data.get('org', 'N/A'), inline=True)
                embed.add_field(name="ISP", value=data.get('isp', 'N/A'), inline=True)
                embed.add_field(name="ASN", value=data.get('asn', 'N/A'), inline=True)
                
                if hostnames := data.get('hostnames', []):
                    embed.add_field(name="Hostnames", value=', '.join(hostnames[:5]) + (' ...' if len(hostnames) > 5 else ''), inline=False)
                if ports := data.get('ports', []):
                    embed.add_field(name=f"Open Ports ({len(ports)})", value=', '.join(map(str, ports[:20])) + (' ...' if len(ports) > 20 else ''), inline=False)
                if vulns := data.get('vulns', []):
                    vuln_text = ', '.join(vulns[:5]) + (f" (+{len(vulns) - 5} more)" if len(vulns) > 5 else "")
                    embed.add_field(name=f"⚠️ Vulnerabilities ({len(vulns)})", value=vuln_text, inline=False)
                if tags := data.get('tags', []):
                    embed.add_field(name="Tags", value=', '.join(tags), inline=False)
                
                embed.set_footer(text="liforra.de | Liforras Utility bot | Powered by Shodan")
                await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
                
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401: await interaction.followup.send("❌ Invalid Shodan API key.", ephemeral=_ephemeral)
            elif e.response.status_code == 404: await interaction.followup.send(f"❌ No information available for `{ip}`.", ephemeral=_ephemeral)
            else: await interaction.followup.send(f"❌ API Error: {e.response.status_code}", ephemeral=_ephemeral)
        except Exception as e:
            tb_str = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
            error_message = f"❌ **An unexpected error occurred:**\n```py\n{tb_str[:1800]}\n```"
            await interaction.followup.send(error_message, ephemeral=_ephemeral)

    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @tree.command(name="help", description="Show available commands")
    @bot.app_commands.describe(_ephemeral="Show the response only to you (default: False)")
    async def help_slash(interaction: discord.Interaction, _ephemeral: bool = False):
        bot.log_command(interaction.user.id, str(interaction.user), "help", [], is_slash=True)
        embed = discord.Embed(title="📚 Command Help", description="Available slash commands for this bot", color=0x3498DB, timestamp=datetime.now())
        embed.add_field(name="🎮 General", value="`/trump`, `/tech`, `/fact`, `/search`\n`/websites`, `/pings`, `/playerinfo`, `/namehistory`", inline=False)
        embed.add_field(name="🌐 Network Tools", value="`/ip`, `/ipdbinfo`, `/ipdblist`, `/ipdbsearch`, `/ipdbstats`\n`/phone`, `/shodan`", inline=False)
        embed.add_field(name="👥 Alt Lookup", value="`/alts` (Rate limited: 2/min)", inline=False)
        if str(interaction.user.id) in bot.config.admin_ids:
            embed.add_field(name="⚙️ Admin", value="`/altsrefresh`, `/ipdbrefresh`, `/reloadconfig`\n`/configget`, `/configset`, `/configdebug`", inline=False)
        embed.add_field(name="💡 Tip", value="Most commands have an `_ephemeral` option to make the response visible only to you.", inline=False)
        embed.set_footer(text="liforra.de | Liforras Utility bot")
        await interaction.response.send_message(embed=embed, ephemeral=_ephemeral)
        
    # ==================== ADMIN COMMANDS ====================

    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @tree.command(name="reloadconfig", description="[ADMIN] Reload all configuration files")
    @bot.app_commands.describe(_ephemeral="Show the response only to you (default: False)")
    async def reloadconfig_slash(interaction: discord.Interaction, _ephemeral: bool = False):
        bot.log_command(interaction.user.id, str(interaction.user), "reloadconfig", [], is_slash=True)
        if not str(interaction.user.id) in bot.config.admin_ids:
            return await interaction.response.send_message("❌ This command is admin-only.", ephemeral=True)
        await interaction.response.defer(ephemeral=_ephemeral)
        try:
            bot.config.load_config()
            bot.load_notes()
            bot.alts_handler.load_and_preprocess_alts_data()
            bot.ip_handler.load_ip_geo_data()
            embed = discord.Embed(title="✅ Config Reloaded", description="Successfully reloaded all configuration files.", color=0x2ECC71, timestamp=datetime.now())
            await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
        except Exception as e:
            tb_str = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
            error_message = f"❌ **Failed to reload config:**\n```py\n{tb_str[:1800]}\n```"
            await interaction.followup.send(error_message, ephemeral=_ephemeral)

# =================================================================================
# END OF SLASH COMMAND REGISTRATION


class Bot:
    def __init__(self, token: str, data_dir: Path, token_type: str = "bot"):
        """Initialize the bot with token and data directory."""
        logger.info("Initializing bot")
        
        self.token = token
        self.data_dir = data_dir
        self.token_type = token_type
        self.command_prefix = ["!", "?", "."]
        
        # Ensure data directory exists
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Data directory ready: {self.data_dir}")
            
            # Set up log file
            self.log_file = self.data_dir / "bot.log"
            file_handler = logging.FileHandler(self.log_file, encoding='utf-8')
            file_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            file_handler.setFormatter(file_formatter)
            file_handler.setLevel(logging.DEBUG)
            
            # Add file handler to root logger
            root_logger = logging.getLogger()
            root_logger.setLevel(logging.DEBUG)
            root_logger.addHandler(file_handler)
            
            logger.info(f"Logging to file: {self.log_file}")
            
        except Exception as e:
            logger.critical("Failed to initialize data directory", exc_info=True)
            raise
        
        self.notes_file = self.data_dir / "notes.json"
        self.user_tokens_file = self.data_dir / "user-tokens.json"

        if token_type == "bot":
            intents = discord.Intents.default()
            intents.message_content = True
            intents.members = True
            intents.presences = True
            self.client = discord.Client(intents=intents)
            self.tree = app_commands.CommandTree(self.client)
            self.app_commands = app_commands
            self.discord = discord
        else:
            import selfcord
            self.client = selfcord.Client()
            self.tree = None
            self.app_commands = None
            self.discord = selfcord

        self.config = ConfigManager(data_dir)
        self.steam_location_handler = SteamLocationHandler()
        self.alts_handler = None
        self.ip_handler = IPHandler(data_dir)
        self.logging_handler = LoggingHandler(data_dir)
        self.oauth_handler = None
        self.phone_handler = None
        self.word_stats_handler = None
        self.mc_server_handler = MCServerHandler(data_dir)
        self.user_commands_handler = UserCommands(self)
        self.admin_commands_handler = AdminCommands(self)

        self.notes_data = {"public": {}, "private": {}}
        self.forward_cache = {}
        self.message_cache = {}
        self.edit_history = {}
        
        self.command_rate_limits = defaultdict(lambda: {"alts": [], "ip": [], "search": [], "phone": []})

        self.user_commands = {
            "trump": self.user_commands_handler.command_trump,
            "websites": self.user_commands_handler.command_websites,
            "pings": self.user_commands_handler.command_pings,
            "note": self.user_commands_handler.command_note,
            "help": self.user_commands_handler.command_help,
            "ip": self.user_commands_handler.command_ip,
            "playerinfo": self.user_commands_handler.command_playerinfo,
            "namehistory": self.user_commands_handler.command_namehistory,
            "alts": self.user_commands_handler.command_alts,
            "phone": self.user_commands_handler.command_phone,
            "shodan": self.user_commands_handler.command_shodan,
            "stats": self.user_commands_handler.command_stats,
            "search": self.user_commands_handler.command_mcsearch,
            "random": self.user_commands_handler.command_mcrandom,
            "playerhistory": self.user_commands_handler.command_mcplayers,
            "ask": self.user_commands_handler.command_ask,
            "!ask": self.user_commands_handler.command_ask,
            "models": self.user_commands_handler.command_models,
            "!models": self.user_commands_handler.command_models
        }
        self.admin_commands = {
            "reload-config": self.admin_commands_handler.command_reload_config,
            "config": self.admin_commands_handler.command_config,
            "resend": self.admin_commands_handler.command_resend,
            "override": self.admin_commands_handler.command_override,
            "alts": self.admin_commands_handler.command_alts,
            "qrlogin": self.admin_commands_handler.command_qrlogin,
            "backfill": self.admin_commands_handler.command_backfill,
            "statsclear": self.admin_commands_handler.command_statsclear,
        }

        self.command_help_texts = {
            "ip": "Usage: {0}ip <info|db> [args]\n• `{0}ip info <ip>` - Live IP lookup\n• `{0}ip db <info|list|search|stats>` - Database operations",
            "alts": "Usage: {0}alts <username> or subcommands\n• `{0}alts <username>` - Lookup alts\n• `{0}alts stats` - Show statistics\n• `{0}alts list [page]` - List all alts",
            "ask": "Usage: {0}ask <question> or @mention with question\nAsk Luma AI any question and get an intelligent response.",
            "!ask": "Same as `{0}ask` - Ask Luma AI any question"
        }
        
        self.client.event(self.on_ready)
        self.client.event(self.on_message)
        self.client.event(self.on_message_edit)
        self.client.event(self.on_message_delete)
        self.client.event(self.on_presence_update)

        if self.token_type == "bot" and self.tree:
            register_slash_commands(self.tree, self)

    async def check_authorization(self, user_id: int) -> bool:
        if self.token_type != "bot" or not self.oauth_handler:
            return True
        return await self.oauth_handler.is_user_authorized(str(user_id))

    def check_rate_limit(self, user_id: int, command: str, limit: int, window: int = 60) -> tuple[bool, int]:
        now = datetime.now()
        user_limits = self.command_rate_limits[user_id][command]
        user_limits[:] = [ts for ts in user_limits if (now - ts).total_seconds() < window]
        if len(user_limits) >= limit:
            wait_time = int((user_limits[0] + timedelta(seconds=window) - now).total_seconds())
            return False, wait_time
        user_limits.append(now)
        return True, 0

    def log_command(self, user_id: int, username: str, command: str, args: list = None, is_slash: bool = False):
        """Logs command usage to command.log file."""
        try:
            log_file = self.data_dir / "command.log"
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cmd_type = "SLASH" if is_slash else "TEXT"
            full_command = f"{command} {' '.join(args)}" if args else command
            
            log_entry = f"[{timestamp}] [{cmd_type}] User: {username} (ID: {user_id}) | Command: {full_command}\n"
            
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(log_entry)
                
        except Exception as e:
            print(f"[{self.client.user}] Error logging command: {e}")

    async def run(self):
        print(f"Starting bot instance ({self.token_type}) in directory: {self.data_dir}")
        self.config.load_config()
        
        if self.token_type == "bot":
            self.oauth_handler = OAuthHandler(
                db_type=self.config.oauth_db_type,
                db_url=self.config.oauth_db_url,
                db_user=self.config.oauth_db_user,
                db_password=self.config.oauth_db_password,
                client_id=self.config.oauth_client_id,
                client_secret=self.config.oauth_client_secret,
            )

        existing_pool = None
        if self.config.stats_db_type == self.config.oauth_db_type == "postgres" and self.oauth_handler and getattr(self.oauth_handler, "pg_pool", None):
            existing_pool = self.oauth_handler.pg_pool

        self.word_stats_handler = WordStatsHandler(
            self.config.stats_db_type,
            self.config.stats_db_url,
            self.config.stats_db_user,
            self.config.stats_db_password,
            existing_pool=existing_pool,
        )

        if self.token_type == "bot" and self.config.oauth_db_type == "postgres":
            self.phone_handler = PhoneHandler(
                data_dir=self.data_dir,
                db_type="postgres",
                db_url=self.config.oauth_db_url,
                db_user=self.config.oauth_db_user,
                db_password=self.config.oauth_db_password,
            )
        else:
            # For user tokens or non-Postgres configs, use JSON storage
            self.phone_handler = PhoneHandler(
                data_dir=self.data_dir,
                db_type="json"
            )
        
        self.alts_handler = AltsHandler(self.data_dir, self.config.default_clean_spigey)
        self.alts_handler.load_and_preprocess_alts_data()
        self.load_notes()

        await self.client.start(self.token)

    def load_notes(self):
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
        try:
            with open(self.notes_file, "w", encoding="utf-8") as f: 
                json.dump(self.notes_data, f, indent=2, ensure_ascii=False)
        except Exception as e: 
            print(f"[{self.data_dir.name}] Error saving notes: {e}")

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

    async def bot_send(self, channel, content=None, files=None, embed=None):
        censored_content = self.censor_text(content, channel.guild.id if hasattr(channel, "guild") and channel.guild else None) if content else ""
        try:
            if not censored_content and not files and not embed: return None
            
            kwargs = {"suppress_embeds": True}
            if censored_content:
                kwargs["content"] = censored_content
            if files:
                kwargs["files"] = files
            if embed and self.token_type == "bot":
                kwargs["embed"] = embed
                kwargs.pop("suppress_embeds", None)
            
            sent_message = None
            if censored_content:
                for i, chunk in enumerate(split_message(censored_content)):
                    current_kwargs = kwargs.copy()
                    current_kwargs['content'] = chunk
                    if i > 0: 
                        current_kwargs.pop('files', None)
                        current_kwargs.pop('embed', None)

                    sent = await channel.send(**current_kwargs)
                    if i == 0: sent_message = sent
                return sent_message
            else:
                 return await channel.send(**kwargs)

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

    async def auto_refresh_alts(self):
        await self.client.wait_until_ready()
        await asyncio.sleep(60)
        while not self.client.is_closed():
            if self.config.alts_refresh_url:
                print(f"[{self.client.user}] Auto-refreshing alts database...")
                try:
                    success = await self.alts_handler.refresh_alts_data(self.config.alts_refresh_url, self.ip_handler)
                    if not success: print(f"[{self.client.user}] Alts refresh failed.")
                except Exception as e:
                    print(f"[{self.client.user}] Error during auto-refresh: {e}")
            await asyncio.sleep(60)

    async def handle_command(self, message, command_name: str, args: list):
        # Log the command
        self.log_command(
            message.author.id,
            str(message.author),
            command_name,
            args,
            is_slash=False
        )
        
        if not await self.check_authorization(message.author.id):
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
            tb_str = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
            error_message = f"❌ **An unexpected error occurred:**\n```py\n{tb_str[:1800]}\n```"
            await self.bot_send(message.channel, content=error_message)

    async def on_ready(self):
        """Initialize components when bot is ready."""
        print(f"Logged in as {self.client.user} (ID: {self.client.user.id})")
        if hasattr(self, 'user_commands_handler'):
            await self.user_commands_handler.update_help_texts()

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
        else: 
            await self.logging_handler.log_dm(message)

        await self._handle_sync_message(message)

        if (
            self.word_stats_handler
            and self.word_stats_handler.available
        ):
            await self.word_stats_handler.record_message(
                message.guild.id if message.guild else None,
                message.author.id,
                message.content,
            )

        if self.token_type != "user": return
        
        gid = message.guild.id if message.guild else None
        if not self.config.get_guild_config(gid, "allow-commands", self.config.default_allow_commands, message.author.id, message.channel.id): return

        prefix = self.config.get_prefix(gid)
        if not message.content.startswith(prefix): return
        
        parts = message.content[len(prefix):].split()
        if not parts: return
        await self.handle_command(message, parts[0].lower(), parts[1:])

    async def handle_asteroide_response(self, message):
        try:
            if re.search(r"\S+ has \d+ alts:", message.content):
                if parsed := self.alts_handler.parse_alts_response(message.content): 
                    self.alts_handler.store_alts_data(parsed)
        except Exception as e: print(f"[{self.client.user}] Error handling Asteroide response: {e}")

    async def on_message_edit(self, before, after):
        if after.author.id == self.client.user.id or not after.guild or after.author.bot: return
        if not self.config.get_guild_config(after.guild.id, "prevent-editing", self.config.default_prevent_editing, after.author.id, after.channel.id): return

        original = self.message_cache.get(after.id, {}).get("content", before.content)
        new = after.content or ""
        if original == new: return

        if not ((abs(len(new) - len(original)) >= 3 or calculate_edit_percentage(original, new) >= 20) and not is_likely_typo(original, new)):
            return

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
        if gid is not None and not self.config.get_guild_config(gid, "prevent-deleting", self.config.default_prevent_deleting, message.author.id, message.channel.id):
            return

        original = self.message_cache.get(message.id, {}).get("content", message.content)
        content_display = f"`{(original or '[Empty Message]').replace('`', '`')}`"
        
        attachments = "\n".join([f"<{att.url}>" for att in message.attachments]) if message.attachments else ""
        if not original and not attachments: return

        try:
            if message.author.id == self.client.user.id:
                await self.bot_send(message.channel, (original or '') + ("\n" + attachments if attachments else ""))
            else:
                await self.bot_send(message.channel, f"{content_display}\ndeleted by <@{message.author.id}>" + ("\n" + attachments if attachments else ""))
        except Exception as e:
            print(f"[{self.client.user}] Error in on_message_delete: {e}")
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
        if not target_channel: return print(f"[{self.client.user}] SYNC ERROR: Could not find sync channel.")

        author_name = f"{message.author.name}#{message.author.discriminator}" if message.author.discriminator != '0' else message.author.name
        header = f"**From `{author_name}`** in `{'DMs' if is_dm else f'{message.guild.name} / #{message.channel.name}'}`"
        
        mention = f"<@{self.config.sync_mention_id}>" if is_ping and self.config.sync_mention_id else ""
        
        import httpx, io
        files = []
        if message.attachments:
            async with httpx.AsyncClient() as http_client:
                for att in message.attachments:
                    try:
                        r = await http_client.get(att.url, timeout=60)
                        r.raise_for_status()
                        files.append(self.discord.File(io.BytesIO(r.content), filename=att.filename))
                    except Exception as e: print(f"[{self.client.user}] SYNC: Failed to download attachment: {e}")
        
        sent_message = await self.bot_send(target_channel, content=f"{header}\n{message.content}\n{mention}", files=files)
        if sent_message:
            self.forward_cache[message.id] = {"forwarded_id": sent_message.id, "timestamp": datetime.now()}