"""User-accessible commands."""

import discord
import httpx
import asyncio
import re
from typing import List
from utils.helpers import format_alt_name, format_alts_grid, is_valid_ip, is_valid_ipv6
from utils.constants import COUNTRY_FLAGS


class UserCommands:
    def __init__(self, bot):
        self.bot = bot

    async def command_trump(self, message: discord.Message, args: List[str]):
        """Fetches a random Trump quote."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.whatdoestrumpthink.com/api/v1/quotes/random",
                    timeout=10,
                )
                response.raise_for_status()
                quote = response.json().get("message", "Could not retrieve a quote.")
                await self.bot.bot_send(
                    message.channel, content=f'"{quote}" ~Donald Trump'
                )
        except Exception as e:
            await self.bot.bot_send(
                message.channel,
                content=f"Sorry, an error occurred: {type(e).__name__}",
            )

    async def _check_and_format_sites(self, sites: List[str], header: str) -> List[str]:
        """Checks website status and formats results."""
        if not sites:
            return []
        results = [f"**{header}**"]
        async with httpx.AsyncClient() as client:
            if not isinstance(sites, list):
                results.append(
                    f"🔴 Configuration error: websites setting is not a list."
                )
                return results
            responses = await asyncio.gather(
                *[client.head(site, timeout=10) for site in sites],
                return_exceptions=True,
            )
            for site, resp in zip(sites, responses):
                if isinstance(resp, httpx.Response) and 200 <= resp.status_code < 400:
                    results.append(f"🟢 `{site}` - Online ({resp.status_code})")
                else:
                    error = (
                        type(resp).__name__
                        if isinstance(resp, Exception)
                        else f"Error {resp.status_code}"
                    )
                    results.append(f"🔴 `{site}` - Offline ({error})")
        return results

    async def command_websites(self, message: discord.Message, args: List[str]):
        """Checks status of configured websites."""
        gid = message.guild.id if message.guild else None
        sites = self.bot.config.get_guild_config(
            gid,
            "websites",
            self.bot.config.default_websites,
            message.author.id,
            message.channel.id,
        )
        friend_sites = self.bot.config.get_guild_config(
            gid,
            "friend_websites",
            self.bot.config.default_friend_websites,
            message.author.id,
            message.channel.id,
        )
        main_res, friend_res = await asyncio.gather(
            self._check_and_format_sites(sites, "Websites"),
            self._check_and_format_sites(friend_sites, "Friends Websites"),
        )
        final_output = []
        if main_res:
            final_output.extend(main_res)
        if friend_res:
            if final_output:
                final_output.append("")
            final_output.extend(friend_res)
        await self.bot.bot_send(
            message.channel,
            content="\n".join(final_output)
            if final_output
            else "No websites are configured.",
        )

    async def command_pings(self, message: discord.Message, args: List[str]):
        """Pings configured devices."""

        async def _ping(hostname: str):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ping",
                    "-c",
                    "1",
                    "-W",
                    "1",
                    hostname,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.wait()
                return f"- `{hostname.replace('.liforra.de', '')}`: {'Responding' if proc.returncode == 0 else 'Unreachable'}"
            except Exception as e:
                return f"- `{hostname.replace('.liforra.de', '')}`: Error ({type(e).__name__})"

        devices = [
            "alhena.liforra.de",
            "sirius.liforra.de",
            "chaosserver.liforra.de",
            "antares.liforra.de",
        ]
        results = [
            "**Device Ping Status:**",
            *await asyncio.gather(*[_ping(dev) for dev in devices]),
        ]
        await self.bot.bot_send(message.channel, content="\n".join(results))

    async def command_note(self, message: discord.Message, args: List[str]):
        """Manages notes (create, get, list, delete)."""
        from datetime import datetime

        p = self.bot.config.get_prefix(message.guild.id if message.guild else None)
        usage = f"Usage: `{p}note <create|get|list|delete> <public|private> [name] [content]`"
        if not args:
            return await self.bot.bot_send(message.channel, content=usage)
        action = args[0].lower()
        if action == "create" and len(args) >= 4:
            visibility, note_name, note_content = (
                args[1].lower(),
                args[2],
                " ".join(args[3:]),
            )
            if visibility not in ["public", "private"]:
                return await self.bot.bot_send(
                    message.channel, "Visibility must be 'public' or 'private'"
                )
            note_data = {
                "content": note_content,
                "created": datetime.now().isoformat(),
                "author": str(message.author.id),
            }
            if visibility == "private":
                self.bot.notes_data["private"].setdefault(str(message.author.id), {})[
                    note_name
                ] = note_data
            else:
                self.bot.notes_data["public"].setdefault(
                    str(message.guild.id) if message.guild else "dm", {}
                )[note_name] = note_data
            self.bot.save_notes()
            await self.bot.bot_send(
                message.channel, content=f"✅ Created {visibility} note '{note_name}'"
            )
        elif action == "get" and len(args) >= 3:
            visibility, note_name = args[1].lower(), args[2]
            note_source = (
                self.bot.notes_data["private"].get(str(message.author.id), {})
                if visibility == "private"
                else self.bot.notes_data["public"].get(
                    str(message.guild.id) if message.guild else "dm", {}
                )
            )
            note = note_source.get(note_name)
            if not note:
                return await self.bot.bot_send(
                    message.channel, content=f"❌ Note '{note_name}' not found"
                )
            note_text = f"**Note: {note_name}**\n{note['content']}\n\n*Created by {note['author']} on {note['created'][:10]}*"
            await self.bot.bot_send(message.channel, content=note_text)
        elif action == "list":
            visibility = args[1].lower() if len(args) > 1 else "both"
            output = []
            if visibility in ["private", "both"] and (
                private_notes := self.bot.notes_data["private"].get(
                    str(message.author.id), {}
                )
            ):
                output.extend(
                    [
                        "**Private Notes:**",
                        *[f"- {name}" for name in private_notes.keys()],
                    ]
                )
            if visibility in ["public", "both"] and (
                public_notes := self.bot.notes_data["public"].get(
                    str(message.guild.id) if message.guild else "dm", {}
                )
            ):
                if output:
                    output.append("")
                output.extend(
                    [
                        "**Public Notes:**",
                        *[f"- {name}" for name in public_notes.keys()],
                    ]
                )
            await self.bot.bot_send(
                message.channel,
                content="\n".join(output) if output else "No notes found",
            )
        elif action == "delete" and len(args) >= 3:
            visibility, note_name = args[1].lower(), args[2]
            deleted = False
            if visibility == "private" and note_name in self.bot.notes_data[
                "private"
            ].get(str(message.author.id), {}):
                del self.bot.notes_data["private"][str(message.author.id)][note_name]
                deleted = True
            elif visibility == "public":
                guild_notes = self.bot.notes_data["public"].get(
                    str(message.guild.id) if message.guild else "dm", {}
                )
                if note_name in guild_notes and (
                    guild_notes[note_name]["author"] == str(message.author.id)
                    or str(message.author.id) in self.bot.config.admin_ids
                ):
                    del guild_notes[note_name]
                    deleted = True
            if deleted:
                self.bot.save_notes()
                await self.bot.bot_send(
                    message.channel,
                    content=f"✅ Deleted {visibility} note '{note_name}'",
                )
            else:
                await self.bot.bot_send(
                    message.channel,
                    content=f"❌ Note '{note_name}' not found or you lack permissions",
                )
        else:
            await self.bot.bot_send(message.channel, content=usage)

    async def command_ip(self, message: discord.Message, args: List[str]):
        """IP information and database management."""

        p = self.bot.config.get_prefix(message.guild.id if message.guild else None)

        if not args:
            return await self.bot.bot_send(
                message.channel,
                content=self.bot.command_help_texts["ip"].format(p=p),
            )

        subcommand = args[0].lower()

        if subcommand == "info":
            if len(args) < 2:
                return await self.bot.bot_send(
                    message.channel, content=f"Usage: `{p}ip info <ip>`"
                )

            ip = args[1]
            if not is_valid_ip(ip):
                return await self.bot.bot_send(
                    message.channel, content="❌ Invalid IP address format"
                )

            await self.bot.bot_send(message.channel, f"⚙️ Fetching info for `{ip}`...")

            ip_data = await self.bot.ip_handler.fetch_ip_info(ip)

            if not ip_data:
                return await self.bot.bot_send(
                    message.channel, content=f"❌ Failed to fetch info for `{ip}`"
                )

            flag = COUNTRY_FLAGS.get(ip_data.get("countryCode", ""), "🌐")

            # Format IP display based on type
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

            # Check for VPN provider
            vpn_provider = self.bot.ip_handler.detect_vpn_provider(
                ip_data.get("isp", ""), ip_data.get("org", "")
            )

            if vpn_provider:
                output.append(f"**VPN Provider:** {vpn_provider}")
            elif ip_data.get("proxy"):
                output.append(f"**Proxy/VPN:** Yes")

            if ip_data.get("hosting"):
                output.append(f"**VPS/Hosting:** Yes")

            output.append("\n*liforra.de | Liforras Utility bot | Powered by ip-api.com*")

            await self.bot.bot_send(message.channel, content="\n".join(output))

        elif subcommand == "db":
            if len(args) < 2:
                return await self.bot.bot_send(
                    message.channel,
                    content=f"Usage: `{p}ip db <info|list|search|refresh|stats> [args]`",
                )

            db_subcommand = args[1].lower()

            # Allow stats for everyone, but require admin for other db commands
            is_admin = str(message.author.id) in self.bot.config.admin_ids
            
            if db_subcommand != "stats" and not is_admin:
                return await self.bot.bot_send(
                    message.channel,
                    content="❌ You need admin permissions to use this command."
                )

            if db_subcommand == "stats":
                total_ips = len(self.bot.ip_handler.ip_geo_data)
                countries = set()
                vpn_count = 0
                hosting_count = 0

                for geo in self.bot.ip_handler.ip_geo_data.values():
                    if geo.get("countryCode"):
                        countries.add(geo["countryCode"])
                    
                    vpn_provider = self.bot.ip_handler.detect_vpn_provider(
                        geo.get("isp", ""), geo.get("org", "")
                    )
                    if vpn_provider or geo.get("proxy"):
                        vpn_count += 1
                    
                    if geo.get("hosting"):
                        hosting_count += 1

                output = [
                    "**IP Database Statistics:**",
                    f"📊 **Total IPs:** {total_ips}",
                    f"🌍 **Unique Countries:** {len(countries)}",
                    f"🔒 **VPN/Proxy IPs:** {vpn_count}",
                    f"☁️ **VPS/Hosting IPs:** {hosting_count}",
                    "",
                    "*liforra.de | Liforras Utility bot*"
                ]

                await self.bot.bot_send(message.channel, content="\n".join(output))

            elif db_subcommand == "info":
                if len(args) < 3:
                    return await self.bot.bot_send(
                        message.channel, content=f"Usage: `{p}ip db info <ip>`"
                    )

                ip = args[2]
                if ip not in self.bot.ip_handler.ip_geo_data:
                    return await self.bot.bot_send(
                        message.channel,
                        content=f"❌ No data for `{ip}` in database",
                    )

                geo = self.bot.ip_handler.ip_geo_data[ip]
                flag = COUNTRY_FLAGS.get(geo.get("countryCode", ""), "🌐")

                # Format IP display based on type
                if is_valid_ipv6(ip):
                    ip_header = f"**Cached IP Information for `{ip}`:**"
                else:
                    ip_header = f"**Cached IP Information for [{ip}](<https://whatismyipaddress.com/ip/{ip}>):**"

                output = [
                    ip_header,
                    f"{flag} **Country:** {geo.get('country', 'N/A')} ({geo.get('countryCode', 'N/A')})",
                    f"**Region:** {geo.get('regionName', 'N/A')}",
                    f"**City:** {geo.get('city', 'N/A')}",
                    f"**ISP:** {geo.get('isp', 'N/A')}",
                    f"**Organization:** {geo.get('org', 'N/A')}",
                ]

                # Check for VPN provider
                vpn_provider = self.bot.ip_handler.detect_vpn_provider(
                    geo.get("isp", ""), geo.get("org", "")
                )

                if vpn_provider:
                    output.append(f"**VPN Provider:** {vpn_provider}")
                elif geo.get("proxy"):
                    output.append(f"**Proxy/VPN:** Yes")

                if geo.get("hosting"):
                    output.append(f"**VPS/Hosting:** Yes")

                output.append(
                    f"**Last Updated:** {geo.get('last_updated', 'N/A')[:10]}"
                )
                
                output.append("\n*liforra.de | Liforras Utility bot*")

                await self.bot.bot_send(message.channel, content="\n".join(output))

            elif db_subcommand == "list":
                page = int(args[2]) if len(args) > 2 and args[2].isdigit() else 1
                per_page = 20

                ips = sorted(self.bot.ip_handler.ip_geo_data.keys())
                start = (page - 1) * per_page
                page_ips = ips[start : start + per_page]
                total_pages = (len(ips) + per_page - 1) // per_page

                if not page_ips:
                    return await self.bot.bot_send(
                        message.channel, content="❌ Page not found"
                    )

                output = [f"**Cached IPs (Page {page}/{total_pages}):**"]
                for ip in page_ips:
                    output.append(f"• {self.bot.ip_handler.format_ip_with_geo(ip)}")

                if total_pages > page:
                    output.append(f"\nUse `{p}ip db list {page + 1}` for next page.")
                
                output.append("\n*liforra.de | Liforras Utility bot*")

                await self.bot.bot_send(message.channel, content="\n".join(output))

            elif db_subcommand == "search":
                if len(args) < 3:
                    return await self.bot.bot_send(
                        message.channel, content=f"Usage: `{p}ip db search <term>`"
                    )

                search_term = " ".join(args[2:]).lower()
                results = []

                for ip, geo in self.bot.ip_handler.ip_geo_data.items():
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
                        results.append(
                            f"• {self.bot.ip_handler.format_ip_with_geo(ip)}"
                        )

                if not results:
                    return await self.bot.bot_send(
                        message.channel,
                        content=f"❌ No IPs found matching '{search_term}'",
                    )

                output = [f"**Search Results for '{search_term}':**"] + results[:25]
                if len(results) > 25:
                    output.append(f"\n*Showing 25 of {len(results)} results*")
                
                output.append("\n*liforra.de | Liforras Utility bot*")

                await self.bot.bot_send(message.channel, content="\n".join(output))

            elif db_subcommand == "refresh":
                await self.bot.bot_send(
                    message.channel, "⚙️ Refreshing all IP geolocation data..."
                )

                all_ips = list(self.bot.ip_handler.ip_geo_data.keys())

                if not all_ips:
                    return await self.bot.bot_send(
                        message.channel, content="❌ No IPs in database to refresh"
                    )

                geo_results = await self.bot.ip_handler.fetch_ip_info_batch(all_ips)

                from datetime import datetime

                timestamp = datetime.now().isoformat()
                for ip, geo_data in geo_results.items():
                    self.bot.ip_handler.ip_geo_data[ip] = {
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

                self.bot.ip_handler.save_ip_geo_data()

                await self.bot.bot_send(
                    message.channel,
                    content=f"✅ Refreshed {len(geo_results)} IP records\n\n*liforra.de | Liforras Utility bot | Powered by ip-api.com*",
                )

            else:
                await self.bot.bot_send(
                    message.channel,
                    content=f"❌ Unknown subcommand. Use `{p}help ip` for usage",
                )

        else:
            await self.bot.bot_send(
                message.channel,
                content=f"❌ Unknown subcommand. Use `{p}help ip` for usage",
            )

    async def command_playerinfo(self, message: discord.Message, args: List[str]):
        """Gets detailed Minecraft player information."""
        if not args:
            p = self.bot.config.get_prefix(message.guild.id if message.guild else None)
            return await self.bot.bot_send(
                message.channel,
                content=f"Usage: `{p}playerinfo <username>`"
            )
        
        username = args[0]
        
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
                    return await self.bot.bot_send(
                        message.channel,
                        content=f"❌ Player `{username}` not found"
                    )
                
                player = data["data"]["player"]
                
                # For selfbots, send as formatted text (no embeds)
                output = [
                    f"🎮 **Player Info: {player['username']}**",
                    f"",
                    f"**🆔 UUID:** `{player['id']}`",
                    f"**🔢 Raw UUID:** `{player['raw_id']}`",
                    f"",
                    f"**🔗 Links:**",
                    f"• NameMC: https://namemc.com/profile/{player['username']}",
                    f"• LabyMod: https://laby.net/@{player['username']}",
                    f"• Avatar: {player['avatar']}",
                    f"• Skin (download): https://crafatar.com/skins/{player['raw_id']}",
                    f"• Full Body (with overlay): https://mc-heads.net/body/{player['raw_id']}/right",
                ]
                
                if player.get('name_history') and len(player['name_history']) > 0:
                    history_list = player['name_history'][:10]
                    history = " → ".join([f"`{name}`" for name in history_list])
                    if len(player['name_history']) > 10:
                        history += f" (+{len(player['name_history']) - 10} more)"
                    output.append(f"\n**📜 Name History:**\n{history}")
                
                cached_at = player['meta'].get('cached_at')
                if cached_at:
                    from datetime import datetime
                    cached_time = datetime.fromtimestamp(cached_at).strftime('%Y-%m-%d %H:%M:%S UTC')
                    output.append(f"\n*liforra.de | Liforras Utility bot | Powered by PlayerDB | Data cached at {cached_time}*")
                else:
                    output.append(f"\n*liforra.de | Liforras Utility bot | Powered by PlayerDB*")
                
                await self.bot.bot_send(message.channel, content="\n".join(output))
                
        except httpx.HTTPStatusError as e:
            await self.bot.bot_send(
                message.channel,
                content=f"❌ API Error: {e.response.status_code}"
            )
        except Exception as e:
            await self.bot.bot_send(
                message.channel,
                content=f"❌ Error: {type(e).__name__}"
            )

    async def command_namehistory(self, message: discord.Message, args: List[str]):
        """Gets Minecraft name history from the API - FIXED VERSION."""
        p = self.bot.config.get_prefix(message.guild.id if message.guild else None)
        
        if not args:
            return await self.bot.bot_send(
                message.channel,
                content=f"Usage: `{p}namehistory <username>`"
            )
        
        username = args[0]
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"https://liforra.de/api/namehistory?username={username}",
                    timeout=15
                )
                response.raise_for_status()
                data = response.json()
                
                if not data.get("history"):
                    return await self.bot.bot_send(
                        message.channel,
                        content=f"❌ No name history found for `{username}`"
                    )
                
                output = [f"📜 **Name History for {username}**"]
                
                if data.get("uuid"):
                    output.append(f"**UUID:** `{data['uuid']}`")
                
                if data.get("last_seen_at"):
                    last_seen = data["last_seen_at"][:19].replace("T", " ")
                    output.append(f"**Last Seen:** {last_seen} UTC")
                
                # Sort by id to ensure correct chronological order
                history = sorted(data["history"], key=lambda x: x.get("id", 0))
                
                output.append(f"\n**Name Changes ({len(history)} recorded):**")
                
                for idx, entry in enumerate(history, 1):
                    name = entry['name']
                    
                    if entry.get("changed_at") is None:
                        # No timestamp means it's either original or current
                        if idx == 1:
                            label = "Original"
                        else:
                            label = "Current"
                    else:
                        # Has timestamp - this is when they changed TO this name
                        label = entry["changed_at"][:10]
                    
                    output.append(f"{idx}. `{name}` - {label}")
                
                output.append(f"\n**Profile Links:**")
                output.append(f"• NameMC: https://namemc.com/profile/{username}")
                if data.get("uuid"):
                    output.append(f"• LabyMod: https://laby.net/@{data['uuid']}")
                
                output.append(f"\n*liforra.de | Liforras Utility bot | Powered by liforra.de Name History API*")
                
                await self.bot.bot_send(message.channel, content="\n".join(output))
                
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                await self.bot.bot_send(
                    message.channel,
                    content="⏱️ Rate limit exceeded. Please wait before trying again."
                )
            else:
                await self.bot.bot_send(
                    message.channel,
                    content=f"❌ API Error: {e.response.status_code}"
                )
        except Exception as e:
            await self.bot.bot_send(
                message.channel,
                content=f"❌ Error: {type(e).__name__}"
            )

    async def command_alts(self, message: discord.Message, args: List[str]):
        """Alts database lookup (user-facing, IPs hidden by default)."""
        p = self.bot.config.get_prefix(message.guild.id if message.guild else None)
        
        if not args:
            return await self.bot.bot_send(
                message.channel,
                content=f"Usage: `{p}alts <username>` or `{p}alts list [page]` or `{p}alts stats`"
            )

        subcommand = args[0].lower()

        if subcommand == "stats":
            total_users = len(self.bot.alts_handler.alts_data)
            all_ips = set().union(
                *(
                    data.get("ips", set())
                    for data in self.bot.alts_handler.alts_data.values()
                )
            )
            stats = f"**Alts DB Stats:**\n- Users: {total_users}\n- Unique IPs: {len(all_ips)}\n- Cached IP Geo Data: {len(self.bot.ip_handler.ip_geo_data)}\n\n*liforra.de | Liforras Utility bot*"
            return await self.bot.bot_send(message.channel, content=stats)

        elif subcommand == "list":
            from utils.helpers import is_valid_ipv4, is_valid_ipv6
            
            users = sorted(
                [
                    user
                    for user in self.bot.alts_handler.alts_data.keys()
                    if not (is_valid_ipv4(user) or is_valid_ipv6(user))
                ]
            )
            page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
            per_page = 20
            start = (page - 1) * per_page
            page_users = users[start : start + per_page]
            total_pages = (len(users) + per_page - 1) // per_page

            if not page_users:
                return await self.bot.bot_send(message.channel, "❌ Page not found")

            output = [f"**Tracked Users (Page {page}/{total_pages}):**"]
            for user in page_users:
                data = self.bot.alts_handler.alts_data[user]
                formatted_user_name = format_alt_name(user)
                output.append(
                    f"• {formatted_user_name} - {len(data.get('alts', []))} alts, {len(data.get('ips', []))} IPs"
                )
            if total_pages > page:
                output.append(f"\nUse `{p}alts list {page + 1}` for next page.")
            
            output.append("\n*liforra.de | Liforras Utility bot*")
            await self.bot.bot_send(message.channel, content="\n".join(output))

        else:
            # Username lookup - IPs hidden for non-admins
            search_term = args[0]
            found_user = None

            lowercase_map = {
                k.lower(): k for k in self.bot.alts_handler.alts_data.keys()
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
                return await self.bot.bot_send(
                    message.channel, f"❌ No data for `{search_term}`"
                )

            data = self.bot.alts_handler.alts_data[found_user]
            alts = sorted(list(data.get("alts", set())))
            ips = sorted(list(data.get("ips", set())))
            
            # Check if admin
            is_admin = str(message.author.id) in self.bot.config.admin_ids

            # Calculate most common non-VPN country
            country_counts = {}
            has_used_vpn = False
            
            for ip in ips:
                if ip in self.bot.ip_handler.ip_geo_data:
                    geo = self.bot.ip_handler.ip_geo_data[ip]
                    
                    # Check if VPN/Proxy/Hosting
                    vpn_provider = self.bot.ip_handler.detect_vpn_provider(geo.get("isp", ""), geo.get("org", ""))
                    is_vpn = vpn_provider or geo.get("proxy") or geo.get("hosting")
                    
                    if is_vpn:
                        has_used_vpn = True
                    else:
                        # Count non-VPN countries (discourage USA)
                        country = geo.get("country")
                        country_code = geo.get("countryCode")
                        if country and country_code:
                            # Reduce USA weight by treating it as 0.3 of a count
                            weight = 0.3 if country_code == "US" else 1.0
                            country_counts[country] = country_counts.get(country, 0) + weight
            
            # Find most common country
            likely_location = None
            if country_counts:
                likely_location = max(country_counts, key=country_counts.get)
                # Get flag for the country
                for ip in ips:
                    if ip in self.bot.ip_handler.ip_geo_data:
                        geo = self.bot.ip_handler.ip_geo_data[ip]
                        if geo.get("country") == likely_location:
                            likely_location = f"{COUNTRY_FLAGS.get(geo.get('countryCode', ''), '🌐')} {likely_location}"
                            break

            formatted_found_user = format_alt_name(found_user)
            output = [f"**Alts data for {formatted_found_user}:**"]
            
            if likely_location:
                output.append(f"**Likely Location:** {likely_location}")
            
            if has_used_vpn:
                output.append(f"**VPN Usage:** 🔒 Yes")

            if alts:
                formatted_alts = [format_alt_name(alt) for alt in alts]
                grid_lines = format_alts_grid(formatted_alts, max_per_line=3)
                output.append(f"\n**Alts ({len(alts)}):**")
                output.extend(grid_lines)

            # Show IP count but hide actual IPs for non-admins
            if ips:
                if is_admin:
                    output.append(f"\n**IPs ({len(ips)}):**")
                    for ip in ips:
                        output.append(f"→ {self.bot.ip_handler.format_ip_with_geo(ip)}")
                else:
                    output.append(f"\n**IPs:** {len(ips)} on record *(use `/alts {search_term} _ip:True` to view - admin only)*")

            output.append(
                f"\n*First seen: {data.get('first_seen', 'N/A')[:10]} | Last updated: {data.get('last_updated', 'N/A')[:10]}*"
            )
            output.append("\n*liforra.de | Liforras Utility bot*")
            await self.bot.bot_send(message.channel, content="\n".join(output))

    async def command_help(self, message: discord.Message, args: List[str]):
        """Shows help information."""
        p = self.bot.config.get_prefix(message.guild.id if message.guild else None)

        if not args:
            user_cmds = ", ".join(f"`{cmd}`" for cmd in self.bot.user_commands.keys())
            help_text = (
                f"**Commands:** {user_cmds}\n*Type `{p}help <command>` for more info.*"
            )
            if str(message.author.id) in self.bot.config.admin_ids:
                admin_cmds = ", ".join(
                    f"`{cmd}`" for cmd in self.bot.admin_commands.keys()
                )
                help_text += f"\n\n**Admin Commands:** {admin_cmds}"
            help_text += "\n\n*liforra.de | Liforras Utility bot*"
            await self.bot.bot_send(message.channel, content=help_text)
        else:
            cmd_name = args[0].lower()
            if cmd_name in self.bot.command_help_texts:
                help_content = self.bot.command_help_texts[cmd_name].format(p=p)
                help_content += "\n\n*liforra.de | Liforras Utility bot*"
                await self.bot.bot_send(
                    message.channel,
                    content=help_content,
                )
            else:
                await self.bot.bot_send(
                    message.channel, content=f"❌ Command `{cmd_name}` not found."
                )