"""Main Bot class with event handlers."""

import asyncio
import re
import json
import logging
from pathlib import Path
from typing import Dict, Optional, Union, List
from datetime import datetime, timedelta
from collections import defaultdict

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


class PaginationView:
    """Pagination view with buttons for navigating pages."""
    
    def __init__(self, embeds: List, discord_module, timeout: int = 180):
        self.discord = discord_module
        self.embeds = embeds
        self.current_page = 0
        self.message = None
        self.timeout = timeout
        self.view = None
        self._create_view()
    
    def _create_view(self):
        """Creates the discord.ui.View with buttons."""
        self.view = self.discord.ui.View(timeout=self.timeout)
        
        # First page button
        first_btn = self.discord.ui.Button(label="⏮️", style=self.discord.ButtonStyle.gray)
        first_btn.callback = self._first_page_callback
        self.view.add_item(first_btn)
        self.first_button = first_btn
        
        # Previous page button
        prev_btn = self.discord.ui.Button(label="◀️", style=self.discord.ButtonStyle.primary)
        prev_btn.callback = self._prev_page_callback
        self.view.add_item(prev_btn)
        self.prev_button = prev_btn
        
        # Next page button
        next_btn = self.discord.ui.Button(label="▶️", style=self.discord.ButtonStyle.primary)
        next_btn.callback = self._next_page_callback
        self.view.add_item(next_btn)
        self.next_button = next_btn
        
        # Last page button
        last_btn = self.discord.ui.Button(label="⏭️", style=self.discord.ButtonStyle.gray)
        last_btn.callback = self._last_page_callback
        self.view.add_item(last_btn)
        self.last_button = last_btn
        
        # Delete button
        delete_btn = self.discord.ui.Button(label="🗑️", style=self.discord.ButtonStyle.danger)
        delete_btn.callback = self._delete_callback
        self.view.add_item(delete_btn)
        
        self._update_buttons()
    
    def _update_buttons(self):
        """Updates button states based on current page."""
        self.first_button.disabled = self.current_page == 0
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == len(self.embeds) - 1
        self.last_button.disabled = self.current_page == len(self.embeds) - 1
    
    async def _first_page_callback(self, interaction):
        self.current_page = 0
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[0], view=self.view)
    
    async def _prev_page_callback(self, interaction):
        self.current_page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self.view)
    
    async def _next_page_callback(self, interaction):
        self.current_page += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self.view)
    
    async def _last_page_callback(self, interaction):
        self.current_page = len(self.embeds) - 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self.view)
    
    async def _delete_callback(self, interaction):
        await interaction.message.delete()


# =================================================================================
# START OF FIX: Moved slash command registration outside the Bot class
# =================================================================================
def register_slash_commands(tree, bot: "Bot"):
    """Registers all slash commands for the bot."""
    
    # Imports needed for the commands
    import httpx
    from utils.helpers import format_alt_name, format_alts_grid, is_valid_ip, is_valid_ipv6
    from utils.constants import COUNTRY_FLAGS

    # ==================== USER COMMANDS ====================
    
    # Trump command with embed
    @tree.command(name="trump", description="Get a random Trump quote")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def trump_slash(interaction: bot.discord.Interaction, _ephemeral: bool = False):
        if not bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(
                bot.oauth_handler.get_authorization_message(interaction.user.mention),
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
                
                embed = bot.discord.Embed(
                    description=f'*"{quote}"*',
                    color=0xFF0000,
                )
                embed.set_author(
                    name="Donald Trump",
                    icon_url="https://upload.wikimedia.org/wikipedia/commons/thumb/5/56/Donald_Trump_official_portrait.jpg/480px-Donald_Trump_official_portrait.jpg"
                )
                embed.set_footer(text="liforra.de | Liforras Utility bot | Powered by What Does Trump Think API")
                
                await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {type(e).__name__}", ephemeral=_ephemeral)

    @tree.command(name="ptrump", description="[Private] Get a random Trump quote")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def ptrump_slash(interaction: bot.discord.Interaction):
        await trump_slash(interaction, _ephemeral=True)

    # Tech command with embed
    @tree.command(name="tech", description="Get a random tech tip or fact")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def tech_slash(interaction: bot.discord.Interaction, _ephemeral: bool = False):
        if not bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(
                bot.oauth_handler.get_authorization_message(interaction.user.mention),
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=_ephemeral)
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://techy-api.vercel.app/api/json",
                    timeout=10,
                )
                response.raise_for_status()
                data = response.json()
                message = data.get("message", "Could not retrieve a tech tip.")
                
                embed = bot.discord.Embed(
                    title="💡 Tech Tip",
                    description=message,
                    color=0x00D4AA,
                )
                embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/2103/2103633.png")
                embed.set_footer(text="liforra.de | Liforras Utility bot | Powered by Techy API")
                
                await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {type(e).__name__}", ephemeral=_ephemeral)

    @tree.command(name="ptech", description="[Private] Get a random tech tip or fact")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def ptech_slash(interaction: bot.discord.Interaction):
        await tech_slash(interaction, _ephemeral=True)

    # Fact command with language support
    @tree.command(name="fact", description="Get a random or daily useless fact")
    @bot.app_commands.describe(
        fact_type="Type of fact to get",
        language="Language for the fact (en or de)"
    )
    @bot.app_commands.choices(fact_type=[
        bot.app_commands.Choice(name="Random", value="random"),
        bot.app_commands.Choice(name="Today's Fact", value="today")
    ])
    @bot.app_commands.choices(language=[
        bot.app_commands.Choice(name="English", value="en"),
        bot.app_commands.Choice(name="German", value="de")
    ])
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def fact_slash(interaction: bot.discord.Interaction, fact_type: str = "random", language: str = "en", _ephemeral: bool = False):
        if not bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(
                bot.oauth_handler.get_authorization_message(interaction.user.mention),
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=_ephemeral)
        
        try:
            base_url = "https://uselessfacts.jsph.pl"
            endpoint = f"/api/v2/facts/{fact_type}"
            params = {"language": language}
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{base_url}{endpoint}",
                    params=params,
                    timeout=10,
                )
                response.raise_for_status()
                data = response.json()
                
                fact_text = data.get("text", "Could not retrieve a fact.")
                fact_id = data.get("id", "")
                source = data.get("source", "")
                source_url = data.get("source_url", "")
                
                # Choose color and title based on fact type
                if fact_type == "today":
                    color = 0xFFD700  # Gold
                    title = "📅 Today's Useless Fact"
                    thumbnail_url = "https://cdn-icons-png.flaticon.com/512/3652/3652191.png"
                else:
                    color = 0xFF6B35  # Orange
                    title = "🤔 Random Useless Fact"
                    thumbnail_url = "https://cdn-icons-png.flaticon.com/512/2103/2103558.png"
                
                embed = bot.discord.Embed(
                    title=title,
                    description=fact_text,
                    color=color,
                )
                
                embed.set_thumbnail(url=thumbnail_url)
                
                if source:
                    if source_url:
                        embed.add_field(
                            name="📚 Source", 
                            value=f"[{source}]({source_url})", 
                            inline=False
                        )
                    else:
                        embed.add_field(name="📚 Source", value=source, inline=False)
                
                if fact_id:
                    embed.set_footer(text=f"liforra.de | Liforras Utility bot | Fact ID: {fact_id} | Language: {language.upper()}")
                else:
                    embed.set_footer(text=f"liforra.de | Liforras Utility bot | Language: {language.upper()}")
                
                await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                await interaction.followup.send("❌ No fact available for the selected criteria.", ephemeral=_ephemeral)
            else:
                await interaction.followup.send(f"❌ API Error: {e.response.status_code}", ephemeral=_ephemeral)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {type(e).__name__}", ephemeral=_ephemeral)

    @tree.command(name="pfact", description="[Private] Get a random or daily useless fact")
    @bot.app_commands.describe(
        fact_type="Type of fact to get",
        language="Language for the fact (en or de)"
    )
    @bot.app_commands.choices(fact_type=[
        bot.app_commands.Choice(name="Random", value="random"),
        bot.app_commands.Choice(name="Today's Fact", value="today")
    ])
    @bot.app_commands.choices(language=[
        bot.app_commands.Choice(name="English", value="en"),
        bot.app_commands.Choice(name="German", value="de")
    ])
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def pfact_slash(interaction: bot.discord.Interaction, fact_type: str = "random", language: str = "en"):
        await fact_slash(interaction, fact_type, language, _ephemeral=True)

    # Search command using SerpAPI - with pagination and better embeds
    @tree.command(name="search", description="Search Google using SerpAPI")
    @bot.app_commands.describe(query="Search query")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def search_slash(interaction: bot.discord.Interaction, query: str, _ephemeral: bool = False):
        if not bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(
                bot.oauth_handler.get_authorization_message(interaction.user.mention),
                ephemeral=True
            )
            return
        
        # Rate limiting: 5 searches per minute
        is_allowed, wait_time = bot.check_rate_limit(interaction.user.id, "search", limit=5, window=60)
        if not is_allowed:
            await interaction.response.send_message(
                f"⏱️ Rate limit exceeded. Please wait {wait_time} seconds before searching again.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=_ephemeral)
        
        if not bot.config.serpapi_key:
            await interaction.followup.send(
                "❌ SerpAPI key not configured. Please contact an administrator.",
                ephemeral=_ephemeral
            )
            return
        
        try:
            params = {
                "q": query,
                "location": "Hamburg, Germany",
                "hl": "de",
                "gl": "de",
                "google_domain": "google.de",
                "api_key": bot.config.serpapi_key
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://serpapi.com/search.json",
                    params=params,
                    timeout=15,
                )
                response.raise_for_status()
                data = response.json()
            
            embeds = []
            
            # --- Build Page 1 (Summary) ---
            summary_embed = bot.discord.Embed(
                title="🔍 Google Search Results",
                description=f"**Query:** `{query}`",
                color=0x4285F4,
                timestamp=datetime.now(),
                url=data.get("search_metadata", {}).get("google_url", None)
            )
            summary_embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/751/751463.png")

            search_info = data.get("search_information", {})
            search_params = data.get("search_parameters", {})
            
            if (total_results := search_info.get("total_results")) or (time_taken := search_info.get("time_taken_displayed")):
                summary_embed.add_field(
                    name="📊 Stats",
                    value=f"{total_results:,} results\n({time_taken or 'N/A'} seconds)",
                    inline=True
                )
            
            if location := search_params.get("location_used"):
                summary_embed.add_field(
                    name="📍 Location",
                    value=location,
                    inline=True
                )

            if answer_box := data.get("answer_box", {}):
                answer = answer_box.get("answer", answer_box.get("snippet", ""))
                if answer:
                    summary_embed.add_field(
                        name="💡 Quick Answer",
                        value=answer[:1000] + ("..." if len(answer) > 1000 else ""),
                        inline=False
                    )

            if kg := data.get("knowledge_graph", {}):
                if kg_title := kg.get("title"):
                    kg_text = f"**{kg_title}**"
                    if kg_type := kg.get("entity_type"):
                        kg_text += f" _{kg_type}_"
                    if kg_desc := kg.get("description"):
                        kg_text += f"\n{kg_desc[:200] + ('...' if len(kg_desc) > 200 else '')}"
                    summary_embed.add_field(name="📚 Knowledge Graph", value=kg_text, inline=False)

            organic_results = data.get("organic_results", [])
            
            if organic_results:
                top_results_text = []
                for result in organic_results[:2]:
                    title = result.get('title', 'No Title')
                    link = result.get('link', '#')
                    snippet = result.get('snippet', 'No snippet available.')
                    top_results_text.append(f"**[{title}]({link})**\n📝 _{snippet[:150] + ('...' if len(snippet) > 150 else '')}_")
                
                summary_embed.add_field(name="🏆 Top Results", value="\n\n".join(top_results_text), inline=False)
            
            embeds.append(summary_embed)

            # --- Build Subsequent Pages (Organic Results) ---
            results_per_page = 4
            remaining_results = organic_results[2:]
            
            if remaining_results:
                num_pages = (len(remaining_results) + results_per_page - 1) // results_per_page
                for i in range(num_pages):
                    chunk = remaining_results[i * results_per_page : (i + 1) * results_per_page]
                    page_embed = bot.discord.Embed(
                        title=f"🔍 Search Results (Page {i+2})",
                        description=f"**Query:** `{query}`",
                        color=0x4285F4
                    )
                    
                    results_text = []
                    for result in chunk:
                        title = result.get('title', 'No Title')
                        link = result.get('link', '#')
                        snippet = result.get('snippet', 'No snippet available.')
                        results_text.append(f"**[{title}]({link})**\n📝 _{snippet[:200] + ('...' if len(snippet) > 200 else '')}_")
                    
                    page_embed.description += "\n\n" + "\n\n".join(results_text)
                    embeds.append(page_embed)

            # --- Send the response ---
            if not embeds:
                await interaction.followup.send("❌ No results found.", ephemeral=_ephemeral)
                return

            for i, embed in enumerate(embeds):
                embed.set_footer(text=f"liforra.de | Liforras Utility bot | Powered by SerpAPI | Page {i+1}/{len(embeds)}")
            
            if len(embeds) == 1:
                await interaction.followup.send(embed=embeds[0], ephemeral=_ephemeral)
            else:
                pagination = PaginationView(embeds, bot.discord)
                await interaction.followup.send(embed=embeds[0], view=pagination.view, ephemeral=_ephemeral)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                await interaction.followup.send("❌ Invalid SerpAPI key configuration.", ephemeral=_ephemeral)
            elif e.response.status_code == 429:
                await interaction.followup.send("❌ SerpAPI rate limit exceeded. Please try again later.", ephemeral=_ephemeral)
            else:
                await interaction.followup.send(f"❌ SerpAPI Error: {e.response.status_code}", ephemeral=_ephemeral)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {type(e).__name__}", ephemeral=_ephemeral)

    @tree.command(name="psearch", description="[Private] Search Google using SerpAPI")
    @bot.app_commands.describe(query="Search query")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def psearch_slash(interaction: bot.discord.Interaction, query: str):
        await search_slash(interaction, query, _ephemeral=True)

    # Websites command with embed
    @tree.command(name="websites", description="Check status of configured websites")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def websites_slash(interaction: bot.discord.Interaction, _ephemeral: bool = False):
        if not bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(
                bot.oauth_handler.get_authorization_message(interaction.user.mention),
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=_ephemeral)
        
        gid = interaction.guild.id if interaction.guild else None
        sites = bot.config.get_guild_config(gid, "websites", bot.config.default_websites, interaction.user.id, interaction.channel.id)
        friend_sites = bot.config.get_guild_config(gid, "friend_websites", bot.config.default_friend_websites, interaction.user.id, interaction.channel.id)
        
        embed = bot.discord.Embed(
            title="🌐 Website Status",
            color=0x3498DB,
            timestamp=datetime.now()
        )
        
        async with httpx.AsyncClient() as client:
            if sites and isinstance(sites, list):
                site_status = []
                responses = await asyncio.gather(
                    *[client.head(site, timeout=10) for site in sites],
                    return_exceptions=True,
                )
                for site, resp in zip(sites, responses):
                    if isinstance(resp, httpx.Response) and 200 <= resp.status_code < 400:
                        site_status.append(f"🟢 `{site}` ({resp.status_code})")
                    else:
                        error = type(resp).__name__ if isinstance(resp, Exception) else f"{resp.status_code}"
                        site_status.append(f"🔴 `{site}` ({error})")
                
                if site_status:
                    embed.add_field(
                        name="Main Websites",
                        value="\n".join(site_status),
                        inline=False
                    )
            
            if friend_sites and isinstance(friend_sites, list):
                friend_status = []
                responses = await asyncio.gather(
                    *[client.head(site, timeout=10) for site in friend_sites],
                    return_exceptions=True,
                )
                for site, resp in zip(friend_sites, responses):
                    if isinstance(resp, httpx.Response) and 200 <= resp.status_code < 400:
                        friend_status.append(f"🟢 `{site}` ({resp.status_code})")
                    else:
                        error = type(resp).__name__ if isinstance(resp, Exception) else f"{resp.status_code}"
                        friend_status.append(f"🔴 `{site}` ({error})")
                
                if friend_status:
                    embed.add_field(
                        name="Friends' Websites",
                        value="\n".join(friend_status),
                        inline=False
                    )
        
        if not embed.fields:
            embed.description = "No websites configured."
        
        embed.set_footer(text="liforra.de | Liforras Utility bot")
        
        await interaction.followup.send(embed=embed, ephemeral=_ephemeral)

    @tree.command(name="pwebsites", description="[Private] Check status of configured websites")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def pwebsites_slash(interaction: bot.discord.Interaction):
        await websites_slash(interaction, _ephemeral=True)

    # Pings command with embed
    @tree.command(name="pings", description="Ping configured devices")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def pings_slash(interaction: bot.discord.Interaction, _ephemeral: bool = False):
        if not bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(bot.oauth_handler.get_authorization_message(interaction.user.mention), ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=_ephemeral)
        
        async def _ping(hostname: str):
            try:
                proc = await asyncio.create_subprocess_exec("ping", "-c", "1", "-W", "1", hostname, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                await proc.wait()
                status = "🟢 Online" if proc.returncode == 0 else "🔴 Offline"
                return (hostname.replace('.liforra.de', ''), status, proc.returncode == 0)
            except Exception as e:
                return (hostname.replace('.liforra.de', ''), f"❌ Error", False)

        devices = ["alhena.liforra.de", "sirius.liforra.de", "chaosserver.liforra.de", "antares.liforra.de"]
        results = await asyncio.gather(*[_ping(dev) for dev in devices])
        
        embed = bot.discord.Embed(
            title="🖥️ Device Status",
            color=0x2ECC71,
            timestamp=datetime.now()
        )
        
        for name, status, is_online in results:
            embed.add_field(
                name=name,
                value=status,
                inline=True
            )
        
        online_count = sum(1 for _, _, is_online in results if is_online)
        embed.set_footer(text=f"liforra.de | Liforras Utility bot | {online_count}/{len(results)} devices responding")
        
        await interaction.followup.send(embed=embed, ephemeral=_ephemeral)

    @tree.command(name="ppings", description="[Private] Ping configured devices")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def ppings_slash(interaction: bot.discord.Interaction):
        await pings_slash(interaction, _ephemeral=True)

    # IP Info command with improved embed
    @tree.command(name="ip", description="Get information about an IP address")
    @bot.app_commands.describe(address="The IP address to look up (IPv4 or IPv6)")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def ip_slash(interaction: bot.discord.Interaction, address: str, _ephemeral: bool = False):
        if not bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(
                bot.oauth_handler.get_authorization_message(interaction.user.mention),
                ephemeral=True
            )
            return
        
        # Rate limiting: 10 requests per minute
        is_allowed, wait_time = bot.check_rate_limit(interaction.user.id, "ip", limit=10, window=60)
        if not is_allowed:
            await interaction.response.send_message(
                f"⏱️ Rate limit exceeded. Please wait {wait_time} seconds before using this command again.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=_ephemeral)
        
        if not is_valid_ip(address):
            await interaction.followup.send("❌ Invalid IP address format", ephemeral=_ephemeral)
            return
        
        ip_data = await bot.ip_handler.fetch_ip_info(address)
        if not ip_data:
            await interaction.followup.send(f"❌ Failed to fetch info for `{address}`", ephemeral=_ephemeral)
            return
        
        flag = COUNTRY_FLAGS.get(ip_data.get("countryCode", ""), "🌐")
        
        embed = bot.discord.Embed(
            title=f"{flag} IP Information",
            description=f"**IP Address:** `{address}`",
            color=0x3498DB,
            timestamp=datetime.now()
        )
        
        if not is_valid_ipv6(address):
            embed.url = f"https://whatismyipaddress.com/ip/{address}"
        
        # Location info
        location_parts = []
        if ip_data.get('city'):
            location_parts.append(ip_data['city'])
        if ip_data.get('regionName'):
            location_parts.append(ip_data['regionName'])
        if ip_data.get('country'):
            location_parts.append(f"{ip_data['country']} ({ip_data.get('countryCode', 'N/A')})")
        
        if location_parts:
            embed.add_field(
                name="📍 Location",
                value=", ".join(location_parts),
                inline=False
            )
        
        # Coordinates and timezone
        details = []
        if ip_data.get('lat') and ip_data.get('lon'):
            details.append(f"**Coordinates:** {ip_data['lat']}, {ip_data['lon']}")
        if ip_data.get('timezone'):
            details.append(f"**Timezone:** {ip_data['timezone']}")
        if ip_data.get('zip'):
            details.append(f"**ZIP:** {ip_data['zip']}")
        
        if details:
            embed.add_field(
                name="🗺️ Details",
                value="\n".join(details),
                inline=False
            )
        
        # Network info
        network_info = []
        if ip_data.get('isp'):
            network_info.append(f"**ISP:** {ip_data['isp']}")
        if ip_data.get('org'):
            network_info.append(f"**Organization:** {ip_data['org']}")
        if ip_data.get('as'):
            network_info.append(f"**AS:** {ip_data['as']}")
        
        if network_info:
            embed.add_field(
                name="🌐 Network",
                value="\n".join(network_info),
                inline=False
            )
        
        # VPN/Proxy detection
        vpn_provider = bot.ip_handler.detect_vpn_provider(ip_data.get("isp", ""), ip_data.get("org", ""))
        security_flags = []
        
        if vpn_provider:
            security_flags.append(f"🔒 **VPN Provider:** {vpn_provider}")
        elif ip_data.get("proxy"):
            security_flags.append("🔒 **Proxy/VPN Detected**")
        
        if ip_data.get("hosting"):
            security_flags.append("☁️ **VPS/Hosting Service**")
        
        if security_flags:
            embed.add_field(
                name="🛡️ Security",
                value="\n".join(security_flags),
                inline=False
            )
        
        embed.set_footer(text="liforra.de | Liforras Utility bot | Powered by ip-api.com")
        
        await interaction.followup.send(embed=embed, ephemeral=_ephemeral)

    @tree.command(name="pip", description="[Private] Get information about an IP address")
    @bot.app_commands.describe(address="The IP address to look up (IPv4 or IPv6)")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def pip_slash(interaction: bot.discord.Interaction, address: str):
        await ip_slash(interaction, address, _ephemeral=True)

    # IP DB Info command
    @tree.command(name="ipdbinfo", description="Get cached information about an IP from database")
    @bot.app_commands.describe(address="The IP address to look up")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def ipdbinfo_slash(interaction: bot.discord.Interaction, address: str, _ephemeral: bool = False):
        if not bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(
                bot.oauth_handler.get_authorization_message(interaction.user.mention),
                ephemeral=True
            )
            return
        
        if address not in bot.ip_handler.ip_geo_data:
            await interaction.response.send_message(
                f"❌ No data for `{address}` in database",
                ephemeral=_ephemeral
            )
            return

        geo = bot.ip_handler.ip_geo_data[address]
        flag = COUNTRY_FLAGS.get(geo.get("countryCode", ""), "🌐")

        embed = bot.discord.Embed(
            title=f"{flag} Cached IP Information",
            description=f"**IP Address:** `{address}`",
            color=0x9B59B6,
            timestamp=datetime.now()
        )

        if not is_valid_ipv6(address):
            embed.url = f"https://whatismyipaddress.com/ip/{address}"

        # Location
        location_parts = []
        if geo.get('city'):
            location_parts.append(geo['city'])
        if geo.get('regionName'):
            location_parts.append(geo['regionName'])
        if geo.get('country'):
            location_parts.append(f"{geo['country']} ({geo.get('countryCode', 'N/A')})")
        
        if location_parts:
            embed.add_field(
                name="📍 Location",
                value=", ".join(location_parts),
                inline=False
            )

        # Network
        network_info = []
        if geo.get('isp'):
            network_info.append(f"**ISP:** {geo['isp']}")
        if geo.get('org'):
            network_info.append(f"**Organization:** {geo['org']}")
        
        if network_info:
            embed.add_field(
                name="🌐 Network",
                value="\n".join(network_info),
                inline=False
            )

        # Security
        vpn_provider = bot.ip_handler.detect_vpn_provider(
            geo.get("isp", ""), geo.get("org", "")
        )
        security_flags = []

        if vpn_provider:
            security_flags.append(f"🔒 **VPN Provider:** {vpn_provider}")
        elif geo.get("proxy"):
            security_flags.append("🔒 **Proxy/VPN Detected**")

        if geo.get("hosting"):
            security_flags.append("☁️ **VPS/Hosting Service**")
        
        if security_flags:
            embed.add_field(
                name="🛡️ Security",
                value="\n".join(security_flags),
                inline=False
            )

        embed.set_footer(text=f"liforra.de | Liforras Utility bot | Last updated: {geo.get('last_updated', 'N/A')[:10]}")

        await interaction.response.send_message(embed=embed, ephemeral=_ephemeral)

    @tree.command(name="pipdbinfo", description="[Private] Get cached information about an IP from database")
    @bot.app_commands.describe(address="The IP address to look up")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def pipdbinfo_slash(interaction: bot.discord.Interaction, address: str):
        await ipdbinfo_slash(interaction, address, _ephemeral=True)

    # IP DB List with pagination
    @tree.command(name="ipdblist", description="List all cached IPs in database")
    @bot.app_commands.describe(page="Page number (default: 1)")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def ipdblist_slash(interaction: bot.discord.Interaction, page: int = 1, _ephemeral: bool = False):
        if not bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(
                bot.oauth_handler.get_authorization_message(interaction.user.mention),
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=_ephemeral)
        
        per_page = 15
        ips = sorted(bot.ip_handler.ip_geo_data.keys())
        total_pages = (len(ips) + per_page - 1) // per_page
        
        if not ips:
            await interaction.followup.send("❌ No IPs in database", ephemeral=_ephemeral)
            return
        
        # Create embeds for all pages
        embeds = []
        for page_num in range(1, total_pages + 1):
            start = (page_num - 1) * per_page
            page_ips = ips[start : start + per_page]
            
            embed = bot.discord.Embed(
                title="🗄️ IP Database",
                description=f"Showing cached IP addresses",
                color=0x9B59B6,
                timestamp=datetime.now()
            )
            
            ip_list = []
            for ip in page_ips:
                ip_list.append(bot.ip_handler.format_ip_with_geo(ip))
            
            embed.add_field(
                name=f"IP Addresses ({len(ips)} total)",
                value="\n".join(ip_list),
                inline=False
            )
            
            embed.set_footer(text=f"liforra.de | Liforras Utility bot | Page {page_num}/{total_pages} • {len(ips)} total IPs")
            embeds.append(embed)
        
        if len(embeds) == 1:
            await interaction.followup.send(embed=embeds[0], ephemeral=_ephemeral)
        else:
            pagination = PaginationView(embeds, bot.discord)
            pagination.current_page = page - 1 if 0 <= page - 1 < len(embeds) else 0
            pagination._update_buttons()
            await interaction.followup.send(embed=embeds[pagination.current_page], view=pagination.view, ephemeral=_ephemeral)

    @tree.command(name="pipdblist", description="[Private] List all cached IPs in database")
    @bot.app_commands.describe(page="Page number (default: 1)")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def pipdblist_slash(interaction: bot.discord.Interaction, page: int = 1):
        await ipdblist_slash(interaction, page, _ephemeral=True)

    # IP DB Search command
    @tree.command(name="ipdbsearch", description="Search IPs by country, city, or ISP")
    @bot.app_commands.describe(term="Search term")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def ipdbsearch_slash(interaction: bot.discord.Interaction, term: str, _ephemeral: bool = False):
        if not bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(
                bot.oauth_handler.get_authorization_message(interaction.user.mention),
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=_ephemeral)
        
        search_term = term.lower()
        results = []

        for ip, geo in bot.ip_handler.ip_geo_data.items():
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
            await interaction.followup.send(
                f"❌ No IPs found matching '{term}'",
                ephemeral=_ephemeral
            )
            return

        # Create paginated embeds
        per_page = 15
        total_pages = (len(results) + per_page - 1) // per_page
        embeds = []

        for page_num in range(1, total_pages + 1):
            start = (page_num - 1) * per_page
            page_results = results[start : start + per_page]

            embed = bot.discord.Embed(
                title=f"🔍 Search Results for '{term}'",
                description=f"Found {len(results)} matching IP(s)",
                color=0xE67E22,
                timestamp=datetime.now()
            )

            ip_list = []
            for ip in page_results:
                ip_list.append(bot.ip_handler.format_ip_with_geo(ip))

            embed.add_field(
                name="Matching IPs",
                value="\n".join(ip_list),
                inline=False
            )

            embed.set_footer(text=f"liforra.de | Liforras Utility bot | Page {page_num}/{total_pages}")
            embeds.append(embed)

        if len(embeds) == 1:
            await interaction.followup.send(embed=embeds[0], ephemeral=_ephemeral)
        else:
            pagination = PaginationView(embeds, bot.discord)
            await interaction.followup.send(embed=embeds[0], view=pagination.view, ephemeral=_ephemeral)

    @tree.command(name="pipdbsearch", description="[Private] Search IPs by country, city, or ISP")
    @bot.app_commands.describe(term="Search term")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def pipdbsearch_slash(interaction: bot.discord.Interaction, term: str):
        await ipdbsearch_slash(interaction, term, _ephemeral=True)

    # IP DB Stats command
    @tree.command(name="ipdbstats", description="Show IP database statistics")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def ipdbstats_slash(interaction: bot.discord.Interaction, _ephemeral: bool = False):
        if not bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(
                bot.oauth_handler.get_authorization_message(interaction.user.mention),
                ephemeral=True
            )
            return
        
        total_ips = len(bot.ip_handler.ip_geo_data)
        countries = set()
        vpn_count = 0
        hosting_count = 0

        for geo in bot.ip_handler.ip_geo_data.values():
            if geo.get("countryCode"):
                countries.add(geo["countryCode"])
            
            vpn_provider = bot.ip_handler.detect_vpn_provider(
                geo.get("isp", ""), geo.get("org", "")
            )
            if vpn_provider or geo.get("proxy"):
                vpn_count += 1
            
            if geo.get("hosting"):
                hosting_count += 1

        embed = bot.discord.Embed(
            title="📊 IP Database Statistics",
            color=0x1ABC9C,
            timestamp=datetime.now()
        )

        embed.add_field(
            name="Total IPs",
            value=f"**{total_ips}**",
            inline=True
        )

        embed.add_field(
            name="Unique Countries",
            value=f"**{len(countries)}**",
            inline=True
        )

        embed.add_field(
            name="VPN/Proxy",
            value=f"**{vpn_count}**",
            inline=True
        )

        embed.add_field(
            name="VPS/Hosting",
            value=f"**{hosting_count}**",
            inline=True
        )

        embed.set_footer(text="liforra.de | Liforras Utility bot")

        await interaction.response.send_message(embed=embed, ephemeral=_ephemeral)

    @tree.command(name="pipdbstats", description="[Private] Show IP database statistics")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def pipdbstats_slash(interaction: bot.discord.Interaction):
        await ipdbstats_slash(interaction, _ephemeral=True)

    # IP DB Refresh command (admin only)
    @tree.command(name="ipdbrefresh", description="[ADMIN] Refresh all IP geolocation data")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def ipdbrefresh_slash(interaction: bot.discord.Interaction, _ephemeral: bool = False):
        if not str(interaction.user.id) in bot.config.admin_ids:
            await interaction.response.send_message("❌ This command is admin-only.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=_ephemeral)
        
        all_ips = list(bot.ip_handler.ip_geo_data.keys())

        if not all_ips:
            await interaction.followup.send("❌ No IPs in database to refresh", ephemeral=_ephemeral)
            return

        geo_results = await bot.ip_handler.fetch_ip_info_batch(all_ips)

        timestamp = datetime.now().isoformat()
        for ip, geo_data in geo_results.items():
            bot.ip_handler.ip_geo_data[ip] = {
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

        bot.ip_handler.save_ip_geo_data()

        embed = bot.discord.Embed(
            title="✅ Refresh Complete",
            description=f"Successfully refreshed **{len(geo_results)}** IP records",
            color=0x2ECC71,
            timestamp=datetime.now()
        )
        
        embed.set_footer(text="liforra.de | Liforras Utility bot | Powered by ip-api.com")

        await interaction.followup.send(embed=embed, ephemeral=_ephemeral)

    @tree.command(name="pipdbrefresh", description="[Private] [ADMIN] Refresh all IP geolocation data")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def pipdbrefresh_slash(interaction: bot.discord.Interaction):
        await ipdbrefresh_slash(interaction, _ephemeral=True)

    # PlayerInfo command with fixed skin and LabyMod link
    @tree.command(name="playerinfo", description="Get detailed information about a Minecraft player")
    @bot.app_commands.describe(username="The Minecraft username to look up")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def playerinfo_slash(interaction: bot.discord.Interaction, username: str, _ephemeral: bool = False):
        if not bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(
                bot.oauth_handler.get_authorization_message(interaction.user.mention),
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
                
                # Handle new API error format for not found
                if (isinstance(data, dict) and data.get("code") == 404) or (data.get("code") != "player.found"):
                    await interaction.followup.send(
                        f"❌ No player found for `{username}`.",
                        ephemeral=_ephemeral
                    )
                    return
                
                player = data["data"]["player"]
                
                embed = bot.discord.Embed(
                    title=f"🎮 {player['username']}",
                    description=f"Minecraft Player Information",
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
                
                # Links with LabyMod
                links = [
                    f"[NameMC](https://namemc.com/profile/{player['username']})",
                    f"[LabyMod](https://laby.net/@{player['username']})",
                    f"[Skin Download](https://crafatar.com/skins/{player['raw_id']})"
                ]
                embed.add_field(
                    name="🔗 Profile Links",
                    value=" • ".join(links),
                    inline=False
                )
                
                # Name History
                if player.get('name_history') and len(player['name_history']) > 0:
                    history_list = player['name_history'][:8]
                    history_text = " → ".join([f"`{name}`" for name in history_list])
                    if len(player['name_history']) > 8:
                        history_text += f"\n*... and {len(player['name_history']) - 8} more names*"
                    
                    embed.add_field(
                        name="📜 Name History",
                        value=history_text,
                        inline=False
                    )
                
                # Fixed skin render - using Crafatar which is more reliable
                skin_render = f"https://crafatar.com/renders/body/{player['raw_id']}?overlay=true&size=512"
                embed.set_image(url=skin_render)
                
                # Footer with cache info
                cached_at = player['meta'].get('cached_at')
                if cached_at:
                    cached_time = datetime.fromtimestamp(cached_at).strftime('%Y-%m-%d %H:%M:%S UTC')
                    embed.set_footer(
                        text=f"liforra.de | Liforras Utility bot | Powered by PlayerDB | Data cached at {cached_time}",
                        icon_url=player['avatar']
                    )
                else:
                    embed.set_footer(
                        text="liforra.de | Liforras Utility bot | Powered by PlayerDB",
                        icon_url=player['avatar']
                    )
                
                await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
                
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 503:
                await interaction.followup.send(
                    "⚠️ The player info service is temporarily unavailable (503). This usually means the API couldn't fetch data from upstream sources or is experiencing issues. Please try again later.",
                    ephemeral=_ephemeral
                )
            else:
                await interaction.followup.send(f"❌ API Error: {e.response.status_code}", ephemeral=_ephemeral)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {type(e).__name__}", ephemeral=_ephemeral)

    @tree.command(name="pplayerinfo", description="[Private] Get detailed information about a Minecraft player")
    @bot.app_commands.describe(username="The Minecraft username to look up")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def pplayerinfo_slash(interaction: bot.discord.Interaction, username: str):
        await playerinfo_slash(interaction, username, _ephemeral=True)

    # Name History command with embed - FIXED VERSION
    @tree.command(name="namehistory", description="Get complete Minecraft name change history")
    @bot.app_commands.describe(username="The Minecraft username to look up")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def namehistory_slash(interaction: bot.discord.Interaction, username: str, _ephemeral: bool = False):
        if not bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(
                bot.oauth_handler.get_authorization_message(interaction.user.mention),
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=_ephemeral)
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"https://liforra.de/api/namehistory?username={username}",
                    timeout=15
                )
                response.raise_for_status()
                data = response.json()
                
                # Handle not found (404) in API response
                if (isinstance(data, dict) and data.get("code") == 404) or not data.get("history"):
                    await interaction.followup.send(
                        f"❌ No name history found for `{username}`. The player does not exist or has no recorded name changes.",
                        ephemeral=_ephemeral
                    )
                    return
                
                embed = bot.discord.Embed(
                    title=f"📜 Name History",
                    description=f"**Player:** {username}",
                    color=0x9B59B6,
                    timestamp=datetime.now(),
                    url=f"https://namemc.com/profile/{username}"
                )
                
                if data.get("uuid"):
                    embed.add_field(
                        name="🆔 UUID",
                        value=f"`{data['uuid']}`",
                        inline=False
                    )
                
                if data.get("last_seen_at"):
                    last_seen = data["last_seen_at"][:19].replace("T", " ")
                    embed.add_field(
                        name="👀 Last Seen",
                        value=f"{last_seen} UTC",
                        inline=True
                    )
                
                # Sort by id to ensure correct chronological order
                history = sorted(data["history"], key=lambda x: x.get("id", 0))
                
                changes_text = []
                for idx, entry in enumerate(history, 1):
                    name = entry['name']
                    if entry.get("changed_at") is None:
                        label = "Original Name"
                    else:
                        label = entry["changed_at"][:19].replace("T", " ") + " UTC"
                    changes_text.append(f"`{idx}.` **{name}** - {label}")
                
                # Limit to first 15 entries to avoid hitting embed limits
                if len(changes_text) > 15:
                    display_text = changes_text[:15]
                    display_text.append(f"*... and {len(changes_text) - 15} more names*")
                else:
                    display_text = changes_text
                
                embed.add_field(
                    name=f"📝 Name Changes ({len(history)} total)",
                    value="\n".join(display_text) if display_text else "No changes found",
                    inline=False
                )
                
                # Profile links
                links = [f"[NameMC](https://namemc.com/profile/{username})"]
                if data.get("uuid"):
                    links.append(f"[LabyMod](https://laby.net/@{data['uuid']})")
                
                embed.add_field(
                    name="🔗 Profile Links",
                    value=" • ".join(links),
                    inline=False
                )
                
                embed.set_footer(text="liforra.de | Liforras Utility bot | Powered by liforra.de Name History API")
                
                await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
                
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                await interaction.followup.send(
                    "⏱️ Rate limit exceeded. Please wait before trying again.",
                    ephemeral=_ephemeral
                )
            elif e.response.status_code == 404:
                await interaction.followup.send(
                    f"❌ No name history found for `{username}`. The player does not exist or has no recorded name changes.",
                    ephemeral=_ephemeral
                )
            elif e.response.status_code == 503:
                await interaction.followup.send(
                    "⚠️ The name history service is temporarily unavailable (503). This usually means the API couldn't fetch data from upstream sources or is experiencing issues. Please try again later.",
                    ephemeral=_ephemeral
                )
            else:
                await interaction.followup.send(
                    f"❌ API Error: {e.response.status_code}",
                    ephemeral=_ephemeral
                )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Error: {type(e).__name__}",
                ephemeral=_ephemeral
            )

    @tree.command(name="pnamehistory", description="[Private] Get complete Minecraft name change history")
    @bot.app_commands.describe(username="The Minecraft username to look up")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def pnamehistory_slash(interaction: bot.discord.Interaction, username: str):
        await namehistory_slash(interaction, username, _ephemeral=True)

    # Alts lookup command - FIXED VERSION
    @tree.command(name="alts", description="Look up a user's known alts")
    @bot.app_commands.describe(
        username="The username to look up",
        _ip="[ADMIN] Show IP addresses (default: False)"
    )
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def alts_slash(interaction: bot.discord.Interaction, username: str, _ip: bool = False, _ephemeral: bool = False):
        if not bot.check_authorization(interaction.user.id):
            await interaction.response.send_message(
                bot.oauth_handler.get_authorization_message(interaction.user.mention),
                ephemeral=True
            )
            return
        
        # Rate limiting: 2 requests per minute
        is_allowed, wait_time = bot.check_rate_limit(interaction.user.id, "alts", limit=2, window=60)
        if not is_allowed:
            await interaction.response.send_message(
                f"⏱️ Rate limit exceeded. Please wait {wait_time} seconds before using this command again.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=_ephemeral)
        
        search_term = username
        found_user = None

        lowercase_map = {
            k.lower(): k for k in bot.alts_handler.alts_data.keys()
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
            await interaction.followup.send(
                f"❌ No data for `{search_term}`",
                ephemeral=_ephemeral
            )
            return

        data = bot.alts_handler.alts_data[found_user]
        alts = sorted(list(data.get("alts", set())))
        ips = sorted(list(data.get("ips", set())))
        
        # Check if admin and if _ip flag is set
        is_admin = str(interaction.user.id) in bot.config.admin_ids
        show_ips = _ip and is_admin

        # Calculate most common non-VPN country - FIXED WITH PROPER IMPORT
        country_counts = defaultdict(lambda: {"count": 0, "code": None})
        
        for ip in ips:
            if ip in bot.ip_handler.ip_geo_data:
                geo = bot.ip_handler.ip_geo_data[ip]
                vpn_provider = bot.ip_handler.detect_vpn_provider(geo.get("isp", ""), geo.get("org", ""))
                is_vpn = vpn_provider or geo.get("proxy") or geo.get("hosting")
                
                if not is_vpn:
                    country = geo.get("country")
                    country_code = geo.get("countryCode")
                    if country and country_code:
                        weight = 0.3 if country_code == "US" else 1.0
                        country_counts[country]["count"] += weight
                        country_counts[country]["code"] = country_code

        likely_location_str = "Unknown"
        if country_counts:
            most_likely_country = max(country_counts, key=lambda c: country_counts[c]["count"])
            country_data = country_counts[most_likely_country]
            flag = COUNTRY_FLAGS.get(country_data['code'], '🌐')
            likely_location_str = f"{flag} {most_likely_country}"

        # --- Start of Bug Fix: Field Generation with Character Limits ---
        
        def generate_fields(title: str, items: List[str], max_items_per_field: int) -> List[Dict]:
            """Splits a list of items into multiple embed fields if they exceed char limits."""
            if not items:
                return []
            
            fields = []
            current_field_value = ""
            item_count_in_field = 0
            
            for item in items:
                # Check if adding the next item would exceed Discord's limit OR our item count limit
                if (len(current_field_value) + len(item) + 1 > 1024) or \
                   (item_count_in_field >= max_items_per_field):
                    fields.append({
                        "name": title if not fields else "\u200b",
                        "value": current_field_value,
                        "inline": False
                    })
                    current_field_value = ""
                    item_count_in_field = 0

                current_field_value += f"{item}\n"
                item_count_in_field += 1
            
            if current_field_value:  # Add the last remaining field
                fields.append({
                    "name": title if not fields else "\u200b",
                    "value": current_field_value,
                    "inline": False
                })
            return fields

        alt_fields = generate_fields(
            f"Known Alts ({len(alts)} total)", 
            [format_alt_name(alt) for alt in alts],
            max_items_per_field=20
        )

        ip_fields = []
        if show_ips:
            ip_fields = generate_fields(
                f"🌐 Known IP Addresses ({len(ips)} total)",
                [bot.ip_handler.format_ip_with_geo(ip) for ip in ips],
                max_items_per_field=15
            )

        all_fields = alt_fields + ip_fields
        
        # --- End of Bug Fix ---
        
        FIELDS_PER_PAGE = 5
        total_pages = (len(all_fields) + FIELDS_PER_PAGE - 1) // FIELDS_PER_PAGE if all_fields else 1
        embeds = []

        for page_num in range(total_pages):
            embed = bot.discord.Embed(
                title="👥 Alt Accounts",
                color=0xE74C3C,
                timestamp=datetime.now(),
                description=(
                    f"**Player:** {format_alt_name(found_user)}\n"
                    f"**First Seen:** {data.get('first_seen', 'N/A')[:10]}\n"
                    f"**Last Updated:** {data.get('last_updated', 'N/A')[:10]}"
                )
            )
            
            # Add location only on the first page
            if page_num == 0:
                 embed.add_field(name="🌍 Location", value=likely_location_str, inline=False)
            
            start_index = page_num * FIELDS_PER_PAGE
            end_index = start_index + FIELDS_PER_PAGE
            page_fields = all_fields[start_index:end_index]
            
            for field in page_fields:
                embed.add_field(name=field["name"], value=field["value"], inline=field["inline"])

            # Handle case where there are no alts and IPs are hidden
            if not alts and not show_ips and page_num == 0:
                embed.add_field(name="Known Alts", value="None found", inline=False)
            
            embed.set_footer(text=f"liforra.de | Liforras Utility bot | Page {page_num + 1}/{total_pages}")
            embeds.append(embed)
        
        # Final fallback for a user with no data at all
        if not embeds:
            embed = bot.discord.Embed(
                title="👥 Alt Accounts",
                color=0xE74C3C,
                timestamp=datetime.now(),
                description=(
                    f"**Player:** {format_alt_name(found_user)}\n"
                    f"**First Seen:** {data.get('first_seen', 'N/A')[:10]}\n"
                    f"**Last Updated:** {data.get('last_updated', 'N/A')[:10]}"
                )
            )
            embed.add_field(name="🌍 Location", value=likely_location_str, inline=False)
            embed.add_field(name="Known Alts", value="None found", inline=False)
            embed.set_footer(text="liforra.de | Liforras Utility bot | Page 1/1")
            embeds.append(embed)

        if len(embeds) == 1:
            await interaction.followup.send(embed=embeds[0], ephemeral=_ephemeral)
        else:
            pagination = PaginationView(embeds, bot.discord)
            await interaction.followup.send(embed=embeds[0], view=pagination.view, ephemeral=_ephemeral)

    @tree.command(name="palts", description="[Private] Look up a user's known alts")
    @bot.app_commands.describe(
        username="The username to look up",
        _ip="[ADMIN] Show IP addresses (default: False)"
    )
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def palts_slash(interaction: bot.discord.Interaction, username: str, _ip: bool = False):
        await alts_slash(interaction, username, _ip, _ephemeral=True)

    # Help command
    @tree.command(name="help", description="Show available commands")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def help_slash(interaction: bot.discord.Interaction, _ephemeral: bool = False):
        embed = bot.discord.Embed(
            title="📚 Command Help",
            description="Available slash commands for this bot",
            color=0x3498DB,
            timestamp=datetime.now()
        )

        embed.add_field(
            name="🎮 General Commands",
            value=(
                "`/trump` - Get a random Trump quote\n"
                "`/tech` - Get a random tech tip\n"
                "`/fact` - Get a useless fact (random or today)\n"
                "`/search <query>` - Search Google (rate limited)\n"
                "`/websites` - Check website status\n"
                "`/pings` - Ping configured devices\n"
                "`/playerinfo <username>` - Get Minecraft player info\n"
                "`/namehistory <username>` - Get complete name change history\n"
                "`/help` - Show this help message"
            ),
            inline=False
        )

        embed.add_field(
            name="🌐 IP Commands",
            value=(
                "`/ip <address>` - Get live IP information\n"
                "`/ipdbinfo <address>` - Get cached IP info\n"
                "`/ipdblist [page]` - List all cached IPs\n"
                "`/ipdbsearch <term>` - Search IPs\n"
                "`/ipdbstats` - Show IP database stats"
            ),
            inline=False
        )
        
        embed.add_field(
            name="👥 Alt Lookup",
            value=(
                "`/alts <username>` - Look up player alts and location\n"
                "*Note: Rate limited to 2/min*"
            ),
            inline=False
        )

        if bot.token_type == "bot" and bot.oauth_handler:
            embed.add_field(
                name="🔒 Authorization",
                value=f"Most commands require authorization. [Click here to authorize]({bot.oauth_handler.oauth_url})",
                inline=False
            )
        
        if str(interaction.user.id) in bot.config.admin_ids:
            embed.add_field(
                name="⚙️ Admin Commands",
                value=(
                    "`/alts <username> _ip:True` - View IPs (admin only)\n"
                    "`/altsrefresh` - Refresh alts database from remote source\n"
                    "`/ipdbrefresh` - Refresh all IP data\n"
                    "`/reloadconfig` - Reload all config files\n"
                    "`/configget <path>` - Get a config value\n"
                    "`/configset <path> <value>` - Set a config value\n"
                    "`/configdebug` - Show debug info"
                ),
                inline=False
            )
        
        embed.add_field(
            name="💡 Tip",
            value="Prefix any command with `p` (e.g., `/pip`, `/palts`) to make the response private (ephemeral)",
            inline=False
        )

        embed.set_footer(text="liforra.de | Liforras Utility bot")

        await interaction.response.send_message(embed=embed, ephemeral=_ephemeral)

    @tree.command(name="phelp", description="[Private] Show available commands")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def phelp_slash(interaction: bot.discord.Interaction):
        await help_slash(interaction, _ephemeral=True)

    # ==================== ADMIN COMMANDS ====================

    # Reload Config command
    @tree.command(name="reloadconfig", description="[ADMIN] Reload all configuration files")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def reloadconfig_slash(interaction: bot.discord.Interaction, _ephemeral: bool = False):
        if not str(interaction.user.id) in bot.config.admin_ids:
            await interaction.response.send_message("❌ This command is admin-only.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=_ephemeral)
        
        try:
            bot.config.load_config()
            bot.load_notes()
            bot.alts_handler.load_and_preprocess_alts_data()
            bot.ip_handler.load_ip_geo_data()
            
            embed = bot.discord.Embed(
                title="✅ Config Reloaded",
                description="Successfully reloaded all configuration files",
                color=0x2ECC71,
                timestamp=datetime.now()
            )
            
            embed.set_footer(text="liforra.de | Liforras Utility bot")
            
            await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to reload config: {e}", ephemeral=_ephemeral)

    @tree.command(name="preloadconfig", description="[Private] [ADMIN] Reload all configuration files")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def preloadconfig_slash(interaction: bot.discord.Interaction):
        await reloadconfig_slash(interaction, _ephemeral=True)

    # Config Get command
    @tree.command(name="configget", description="[ADMIN] Get a configuration value")
    @bot.app_commands.describe(path="Config path (e.g., general.prefix)")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def configget_slash(interaction: bot.discord.Interaction, path: str, _ephemeral: bool = False):
        if not str(interaction.user.id) in bot.config.admin_ids:
            await interaction.response.send_message("❌ This command is admin-only.", ephemeral=True)
            return
        
        try:
            value = bot.config.config_data
            for key in path.split("."):
                value = value[key]
            censored_value = bot.config.censor_recursive(path, value)
            display_str = (
                json.dumps(censored_value, indent=2)
                if isinstance(censored_value, dict)
                else f"`{censored_value}`"
            )
            if isinstance(censored_value, dict):
                await interaction.response.send_message(
                    f"✅ `{path}` =\n```json\n{display_str}\n```",
                    ephemeral=_ephemeral
                )
            else:
                await interaction.response.send_message(
                    f"✅ `{path}` = {display_str}",
                    ephemeral=_ephemeral
                )
        except (KeyError, TypeError):
            await interaction.response.send_message(f"❌ Path not found: `{path}`", ephemeral=_ephemeral)

    @tree.command(name="pconfigget", description="[Private] [ADMIN] Get a configuration value")
    @bot.app_commands.describe(path="Config path (e.g., general.prefix)")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def pconfigget_slash(interaction: bot.discord.Interaction, path: str):
        await configget_slash(interaction, path, _ephemeral=True)

    # Config Set command
    @tree.command(name="configset", description="[ADMIN] Set a configuration value")
    @bot.app_commands.describe(path="Config path (e.g., general.prefix)", value="New value")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def configset_slash(interaction: bot.discord.Interaction, path: str, value: str, _ephemeral: bool = False):
        if not str(interaction.user.id) in bot.config.admin_ids:
            await interaction.response.send_message("❌ This command is admin-only.", ephemeral=True)
            return
        
        if path in bot.config.censor_config:
            await interaction.response.send_message(
                f"❌ Cannot set a censored config key: `{path}`",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=_ephemeral)
        
        try:
            import toml
            keys, target = path.split("."), bot.config.config_data
            for key in keys[:-1]:
                target = target.setdefault(key, {})
            target[keys[-1]] = bot.config.parse_value(value)
            with open(bot.config.config_file, "w", encoding="utf-8") as f:
                toml.dump(bot.config.config_data, f)
            await interaction.followup.send(
                f"✅ Set `{path}` to `{target[keys[-1]]}` and saved.",
                ephemeral=_ephemeral
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to set config: {e}", ephemeral=_ephemeral)

    @tree.command(name="pconfigset", description="[Private] [ADMIN] Set a configuration value")
    @bot.app_commands.describe(path="Config path (e.g., general.prefix)", value="New value")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def pconfigset_slash(interaction: bot.discord.Interaction, path: str, value: str):
        await configset_slash(interaction, path, value, _ephemeral=True)

    # Config Debug command
    @tree.command(name="configdebug", description="[ADMIN] Show debug configuration info")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def configdebug_slash(interaction: bot.discord.Interaction, _ephemeral: bool = False):
        if not str(interaction.user.id) in bot.config.admin_ids:
            await interaction.response.send_message("❌ This command is admin-only.", ephemeral=True)
            return
        
        gid = interaction.guild.id if interaction.guild else None
        uid = interaction.user.id
        cid = interaction.channel.id
        
        debug_info = f"""```ini
[Debug Info for {bot.client.user}]
Is Admin = {str(uid) in bot.config.admin_ids}
Prefix = {bot.config.get_prefix(gid)}
Message Log = {bot.config.get_guild_config(gid, "message-log", bot.config.default_message_log, uid, cid)}
Attachment Log = {bot.config.get_attachment_log_setting(gid, uid, cid)}
Prevent Deleting = {bot.config.get_guild_config(gid, "prevent-deleting", bot.config.default_prevent_deleting, uid, cid)}
Prevent Editing = {bot.config.get_guild_config(gid, "prevent-editing", bot.config.default_prevent_editing, uid, cid)}
Allow Swears = {bot.config.get_guild_config(gid, "allow-swears", bot.config.default_allow_swears, uid, cid)}
Allow Slurs = {bot.config.get_guild_config(gid, "allow-slurs", bot.config.default_allow_slurs, uid, cid)}
Detect IPs = {bot.config.get_guild_config(gid, "detect-ips", bot.config.default_detect_ips, uid, cid)}
Clean Spigey Data = {bot.config.default_clean_spigey}
Match Status = {bot.config.match_status}
```"""
        await interaction.response.send_message(debug_info, ephemeral=_ephemeral)

    @tree.command(name="pconfigdebug", description="[Private] [ADMIN] Show debug configuration info")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def pconfigdebug_slash(interaction: bot.discord.Interaction):
        await configdebug_slash(interaction, _ephemeral=True)

    # Alts Refresh command (admin only)
    @tree.command(name="altsrefresh", description="[ADMIN] Manually refresh alts database from remote source")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def altsrefresh_slash(interaction: bot.discord.Interaction, _ephemeral: bool = False):
        if not str(interaction.user.id) in bot.config.admin_ids:
            await interaction.response.send_message("❌ This command is admin-only.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=_ephemeral)
        
        if not bot.config.alts_refresh_url:
            await interaction.followup.send(
                "❌ Alts refresh URL not configured in `config.toml`",
                ephemeral=_ephemeral
            )
            return
        
        embed = bot.discord.Embed(
            title="⚙️ Refreshing Alts Database",
            description="Fetching data from remote source...",
            color=0xF39C12,
            timestamp=datetime.now()
        )
        
        embed.set_footer(text="liforra.de | Liforras Utility bot")
        
        status_msg = await interaction.followup.send(embed=embed, ephemeral=_ephemeral)
        
        success = await bot.alts_handler.refresh_alts_data(
            bot.config.alts_refresh_url, bot.ip_handler
        )
        
        if success:
            total_users = len(bot.alts_handler.alts_data)
            all_ips = set().union(
                *(
                    data.get("ips", set())
                    for data in bot.alts_handler.alts_data.values()
                )
            )
            
            embed = bot.discord.Embed(
                title="✅ Alts Database Refreshed",
                description="Successfully fetched and merged remote data",
                color=0x2ECC71,
                timestamp=datetime.now()
            )
            
            embed.add_field(
                name="📊 Database Stats",
                value=(
                    f"**Total Users:** {total_users}\n"
                    f"**Unique IPs:** {len(all_ips)}\n"
                    f"**Cached IP Geo Data:** {len(bot.ip_handler.ip_geo_data)}"
                ),
                inline=False
            )
            
            embed.set_footer(text="liforra.de | Liforras Utility bot")
            
            await status_msg.edit(embed=embed)
        else:
            embed = bot.discord.Embed(
                title="❌ Refresh Failed",
                description="Could not fetch data from remote source. Check logs for details.",
                color=0xE74C3C,
                timestamp=datetime.now()
            )
            
            embed.set_footer(text="liforra.de | Liforras Utility bot")
            
            await status_msg.edit(embed=embed)

    @tree.command(name="paltsrefresh", description="[Private] [ADMIN] Manually refresh alts database from remote source")
    @bot.app_commands.allowed_installs(guilds=True, users=True)
    @bot.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def paltsrefresh_slash(interaction: bot.discord.Interaction):
        await altsrefresh_slash(interaction, _ephemeral=True)
# =================================================================================
# END OF FIX
# =================================================================================


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
        
        # OAuth is only required for bot tokens - will be initialized after config load
        self.oauth_handler = None

        # Initialize command handlers
        self.user_commands_handler = UserCommands(self)
        self.admin_commands_handler = AdminCommands(self)

        # Data storage
        self.notes_data = {"public": {}, "private": {}}
        self.forward_cache = {}
        self.message_cache = {}
        self.edit_history = {}
        
        # Rate limiting - updated to include search
        self.command_rate_limits = defaultdict(lambda: {"alts": [], "ip": [], "search": []})

        # Command mappings (for selfbots only)
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
            "namehistory": "Usage: `{p}namehistory <username>`\nGets the complete name change history for a Minecraft player, including timestamps and profile links.",
            "ip": "Usage: `{p}ip <info|db> [args]`\n- `info <ip>`: Fetches live information about an IP address (supports IPv4 and IPv6).\n- `db info <ip>`: Shows cached IP information from the database.\n- `db list [page]`: Lists all IPs in the database.\n- `db search <term>`: Searches IPs by country, city, or ISP.\n- `db refresh`: Updates all cached IP information.\n- `db stats`: Shows database statistics.",
        }

        # Register event handlers
        self.client.event(self.on_ready)
        self.client.event(self.on_message)
        self.client.event(self.on_message_edit)
        self.client.event(self.on_message_delete)
        self.client.event(self.on_presence_update)

        # Register slash commands if bot token
        if self.token_type == "bot" and self.tree:
            register_slash_commands(self.tree, self)

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

    def check_rate_limit(self, user_id: int, command: str, limit: int, window: int = 60) -> tuple[bool, int]:
        """
        Checks if user has exceeded rate limit.
        Returns (is_allowed, seconds_until_reset).
        """
        now = datetime.now()
        user_limits = self.command_rate_limits[user_id][command]
        
        # Remove old entries outside the window
        user_limits[:] = [timestamp for timestamp in user_limits if now - timestamp < timedelta(seconds=window)]
        
        if len(user_limits) >= limit:
            oldest = user_limits[0]
            wait_time = int((oldest + timedelta(seconds=window) - now).total_seconds())
            return False, wait_time
        
        user_limits.append(now)
        return True, 0

    async def run(self):
        """Starts the bot."""
        print(f"Starting bot instance ({self.token_type}) in directory: {self.data_dir}")
        self.config.load_config()
        
        # Initialize OAuth handler after config is loaded (bot tokens only)
        if self.token_type == "bot":
            self.oauth_handler = OAuthHandler(
                db_type=self.config.oauth_db_type,
                db_url=self.config.oauth_db_url,
                db_user=self.config.oauth_db_user,
                db_password=self.config.oauth_db_password,
            )
        
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

    async def auto_refresh_alts(self):
        """Automatically refreshes alts database every minute."""
        await self.client.wait_until_ready()
        
        # Wait 60 seconds before first refresh to allow bot to fully initialize
        await asyncio.sleep(60)
        
        while not self.client.is_closed():
            if self.config.alts_refresh_url:
                print(f"[{self.client.user}] Auto-refreshing alts database...")
                try:
                    success = await self.alts_handler.refresh_alts_data(
                        self.config.alts_refresh_url, self.ip_handler
                    )
                    if success:
                        total_users = len(self.alts_handler.alts_data)
                        all_ips = set().union(
                            *(
                                data.get("ips", set())
                                for data in self.alts_handler.alts_data.values()
                            )
                        )
                        print(f"[{self.client.user}] Alts refresh complete: {total_users} users, {len(all_ips)} IPs")
                    else:
                        print(f"[{self.client.user}] Alts refresh failed. Check logs.")
                except Exception as e:
                    print(f"[{self.client.user}] Error during auto-refresh: {e}")
            else:
                print(f"[{self.client.user}] Skipping alts auto-refresh (URL not configured)")
            
            # Wait 60 seconds before next refresh
            await asyncio.sleep(60)

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

        # Start background tasks
        self.client.loop.create_task(self.cleanup_forward_cache())
        self.client.loop.create_task(self.cleanup_message_cache())
        self.client.loop.create_task(self.auto_refresh_alts())
        print(f"[{self.client.user}] Started background tasks (cache cleanup, alts auto-refresh)")

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

        # Only process text commands for selfbots (user tokens)
        if self.token_type != "user":
            return
        
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
        if gid is not None and not self.config.get_guild_config(gid, "prevent-deleting", self.config.default_prevent_deleting, message.author.id, message.channel.id):
            return

        original = self.message_cache.get(message.id, {}).get("content", message.content)
        final = message.content

        content_display = f"`{(final or original or '[Empty Message]').replace('`', '`')}`"
        if original and final and original != final:
            content_display = f"**Original:** `{original.replace('`', '`')}`\n**Final:** `{final.replace('`', '`')}`"

        attachments = "\n\n**Attachments:**\n" + "\n".join([f"<{att.url}>" for att in message.attachments]) if message.attachments else ""
        if not original and not final and not attachments:
            return

        try:
            # If the selfbot deleted its own message, only resend the exact same message (no 'deleted by' note)
            if message.author.id == self.client.user.id:
                await self.bot_send(message.channel, (final or original or '[Empty Message]') + (attachments if attachments else ""))
            else:
                await self.bot_send(message.channel, f"{content_display}\ndeleted by <@{message.author.id}>{attachments}")
        except Exception as e:
            print(f"[{self.client.user}] Error in on_message_delete: {e}")
        finally:
            if message.id in self.message_cache:
                del self.message_cache[message.id]
            if message.id in self.edit_history:
                del self.edit_history[message.id]

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
        header_parts = [f"**From `{author_name}`**"]
        if message.guild: header_parts.append(f"in `{message.guild.name}` / <#{message.channel.id}>")
        else: header_parts.append("in `DMs`")
        header = " ".join(header_parts)

        mention = f"<@{self.config.sync_mention_id}>" if is_ping and self.config.sync_mention_id else ""
        
        full_content = f"{header}\n{message.content}\n{mention}"
        
        files = []
        if message.attachments:
            import io
            import httpx
            async with httpx.AsyncClient() as http_client:
                for att in message.attachments:
                    try:
                        response = await http_client.get(att.url, timeout=60)
                        response.raise_for_status()
                        files.append(self.discord.File(io.BytesIO(response.content), filename=att.filename))
                    except Exception as e: print(f"[{self.client.user}] SYNC: Failed to re-download attachment {att.filename}: {e}")
        
        sent_message = await self.bot_send(target_channel, content=full_content, files=files)
        if sent_message:
            self.forward_cache[message.id] = {"forwarded_id": sent_message.id, "timestamp": datetime.now()}