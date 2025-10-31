"""Admin-only commands."""

import discord
import toml
import json
import re
import io
import httpx
import asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from utils.helpers import (
    format_alt_name,
    format_alts_grid,
    is_valid_ipv4,
    is_valid_ipv6,
)
from handlers.qr_login import DiscordQRLogin


class AdminCommands:
    def __init__(self, bot):
        self.bot = bot
        self.ai_channels_file = Path(__file__).parent.parent / "config" / "ai_channels.json"

    def _load_ai_channels(self) -> List[int]:
        if not self.ai_channels_file.exists():
            return []
        with open(self.ai_channels_file, "r") as f:
            return json.load(f)

    def _save_ai_channels(self, channels: List[int]):
        with open(self.ai_channels_file, "w") as f:
            json.dump(channels, f)

    async def command_set_ai(self, message: discord.Message, args: List[str]):
        """Sets a channel to be an AI channel."""
        channel_id = None
        if args and args[0].isdigit():
            channel_id = int(args[0])
        else:
            channel_id = message.channel.id

        ai_channels = self._load_ai_channels()
        if channel_id not in ai_channels:
            ai_channels.append(channel_id)
            self._save_ai_channels(ai_channels)
            await self.bot.bot_send(message.channel, content=f"✅ Channel <#{channel_id}> is now an AI channel.")
        else:
            await self.bot.bot_send(message.channel, content=f"ℹ️ Channel <#{channel_id}> is already an AI channel.")

    async def command_unset_ai(self, message: discord.Message, args: List[str]):
        """Unsets a channel as an AI channel."""
        channel_id = None
        if args and args[0].isdigit():
            channel_id = int(args[0])
        else:
            channel_id = message.channel.id

        ai_channels = self._load_ai_channels()
        if channel_id in ai_channels:
            ai_channels.remove(channel_id)
            self._save_ai_channels(ai_channels)
            await self.bot.bot_send(message.channel, content=f"✅ Channel <#{channel_id}> is no longer an AI channel.")
        else:
            await self.bot.bot_send(message.channel, content=f"ℹ️ Channel <#{channel_id}> is not an AI channel.")

    async def command_reload_config(self, message: discord.Message, args: List[str]):
        """Reloads all configuration files."""
        try:
            self.bot.config.load_config()
            self.bot.load_notes()
            self.bot.alts_handler.load_and_preprocess_alts_data()
            self.bot.ip_handler.load_ip_geo_data()
            await self.bot.bot_send(
                message.channel,
                content="✅ Config, notes, alts, and IP data reloaded!",
            )
        except Exception as e:
            await self.bot.bot_send(
                message.channel, content=f"❌ Failed to reload config: {e}"
            )

    async def command_override(self, message: discord.Message, args: List[str]):
        """Sets user or channel-specific overrides."""
        p = self.bot.config.get_prefix(message.guild.id if message.guild else None)
        if len(args) < 4 or not message.guild:
            return await self.bot.bot_send(
                message.channel,
                f"Usage: `{p}override <user|channel> <@mention> <setting> <value>`",
            )
        override_type, target, setting, value = (
            args[0].lower(),
            args[1],
            args[2],
            self.bot.config.parse_value(" ".join(args[3:])),
        )
        guild_id = str(message.guild.id)
        self.bot.config.config_data.setdefault("guild", {}).setdefault(guild_id, {})
        if override_type in ["user", "channel"] and (
            match := re.search(r"\d+", target)
        ):
            override_key = f"{override_type}_overrides"
            self.bot.config.config_data["guild"][guild_id].setdefault(
                override_key, {}
            ).setdefault(match.group(), {})[setting] = value
            try:
                with open(self.bot.config.config_file, "w", encoding="utf-8") as f:
                    toml.dump(self.bot.config.config_data, f)
                await self.bot.bot_send(
                    message.channel,
                    f"✅ Set {override_type} override: {setting} = {value}",
                )
            except Exception as e:
                await self.bot.bot_send(
                    message.channel, f"❌ Failed to save override: {e}"
                )
        else:
            await self.bot.bot_send(
                message.channel, "❌ Invalid override type or target mention."
            )

    async def command_config(self, message: discord.Message, args: List[str]):
        """Configuration management."""
        p = self.bot.config.get_prefix(message.guild.id if message.guild else None)
        if not args or args[0] not in ["get", "set", "debug"]:
            return await self.bot.bot_send(
                message.channel,
                f"Usage: `{p}config <get|set|debug> [path] [value]`",
            )

        subcommand = args[0]
        if subcommand == "get":
            if len(args) < 2:
                return await self.bot.bot_send(
                    message.channel, f"Usage: `{p}config get <path>`"
                )
            path_to_get = args[1]
            try:
                value = self.bot.config.config_data
                for key in path_to_get.split("."):
                    value = value[key]
                censored_value = self.bot.config.censor_recursive(path_to_get, value)
                display_str = (
                    json.dumps(censored_value, indent=2)
                    if isinstance(censored_value, dict)
                    else f"`{censored_value}`"
                )
                if isinstance(censored_value, dict):
                    await self.bot.bot_send(
                        message.channel,
                        content=f"✅ `{path_to_get}` =\n```json\n{display_str}\n```",
                    )
                else:
                    await self.bot.bot_send(
                        message.channel,
                        content=f"✅ `{path_to_get}` = {display_str}",
                    )
            except (KeyError, TypeError):
                await self.bot.bot_send(
                    message.channel, content=f"❌ Path not found: `{path_to_get}`"
                )
        elif subcommand == "set":
            if len(args) < 3:
                return await self.bot.bot_send(
                    message.channel, f"Usage: `{p}config set <path> <value>`"
                )
            path, new_value_str = args[1], " ".join(args[2:])
            if path in self.bot.config.censor_config:
                return await self.bot.bot_send(
                    message.channel,
                    content=f"❌ Cannot set a censored config key: `{path}`",
                )
            try:
                keys, target = path.split("."), self.bot.config.config_data
                for key in keys[:-1]:
                    target = target.setdefault(key, {})
                target[keys[-1]] = self.bot.config.parse_value(new_value_str)
                with open(self.bot.config.config_file, "w", encoding="utf-8") as f:
                    toml.dump(self.bot.config.config_data, f)
                await self.bot.bot_send(
                    message.channel,
                    f"✅ Set `{path}` to `{target[keys[-1]]}` and saved.",
                )
            except Exception as e:
                await self.bot.bot_send(
                    message.channel, content=f"❌ Failed to set config: {e}"
                )
        elif subcommand == "debug":
            gid, uid, cid = (
                (message.guild.id, message.author.id, message.channel.id)
                if message.guild
                else (None, message.author.id, message.channel.id)
            )
            debug_info = f"""```ini
[Debug Info for {self.bot.client.user}]
Is Admin = {str(uid) in self.bot.config.admin_ids}
Prefix = {self.bot.config.get_prefix(gid)}
Message Log = {self.bot.config.get_guild_config(gid, "message-log", self.bot.config.default_message_log, uid, cid)}
Attachment Log = {self.bot.config.get_attachment_log_setting(gid, uid, cid)}
Prevent Deleting = {self.bot.config.get_guild_config(gid, "prevent-deleting", self.bot.config.default_prevent_deleting, uid, cid)}
Prevent Editing = {self.bot.config.get_guild_config(gid, "prevent-editing", self.bot.config.default_prevent_editing, uid, cid)}
Allow Swears = {self.bot.config.get_guild_config(gid, "allow-swears", self.bot.config.default_allow_swears, uid, cid)}
Allow Slurs = {self.bot.config.get_guild_config(gid, "allow-slurs", self.bot.config.default_allow_slurs, uid, cid)}
Detect IPs = {self.bot.config.get_guild_config(gid, "detect-ips", self.bot.config.default_detect_ips, uid, cid)}
Clean Spigey Data = {self.bot.config.default_clean_spigey}
Match Status = {self.bot.config.match_status}
```"""
            await self.bot.bot_send(message.channel, content=debug_info)

    async def command_resend(self, message: discord.Message, args: List[str]):
        """Resends recent bot messages."""
        try:
            if not args or not args[0].isdigit():
                return await self.bot.bot_send(
                    message.channel,
                    f"Usage: `{self.bot.config.get_prefix(message.guild.id)}resend <number>`",
                )
            num_to_resend = int(args[0])
            if not (0 < num_to_resend <= 25):
                return await self.bot.bot_send(
                    message.channel, "❌ Number must be between 1 and 25."
                )
            await message.delete()
            history = message.channel.history(limit=200)
            bot_messages = [
                msg async for msg in history if msg.author == self.bot.client.user
            ][:num_to_resend]
            for msg in reversed(bot_messages):
                files = []
                if msg.attachments:
                    async with httpx.AsyncClient() as http_client:
                        for att in msg.attachments:
                            try:
                                response = await http_client.get(att.url, timeout=60)
                                response.raise_for_status()
                                files.append(
                                    discord.File(
                                        io.BytesIO(response.content),
                                        filename=att.filename,
                                    )
                                )
                            except Exception as e:
                                print(
                                    f"[{self.bot.client.user}] Failed to re-download attachment {att.filename}: {e}"
                                )
                await self.bot.bot_send(
                    message.channel, content=msg.content, files=files
                )
                await asyncio.sleep(0.5)
        except Exception as e:
            print(f"[{self.bot.client.user}] Error in resend command: {e}")
            await self.bot.bot_send(
                message.channel, content="❌ An error occurred while resending."
            )

    async def command_backfill(self, message: discord.Message, args: List[str]):
        """Backfills word statistics for the current channel."""
        handler = getattr(self.bot, "word_stats_handler", None)
        if not handler or not handler.available:
            return await self.bot.bot_send(
                message.channel,
                content="❌ Word statistics database is not configured.",
            )

        if not message.guild:
            return await self.bot.bot_send(
                message.channel, content="❌ Backfill can only be used in servers."
            )

        days: Optional[int] = 7
        if args:
            token = args[0].lower()
            if token in {"all", "infinite", "full", "*"}:
                days = None
            elif token.isdigit():
                days = int(token)
            else:
                return await self.bot.bot_send(
                    message.channel,
                    content="❌ Invalid days value. Use a positive number or `all`.",
                )

        if days is not None and days < 1:
            days = 7

        span_text = "all available history" if days is None else f"the last {days} day(s)"
        await self.bot.bot_send(
            message.channel, content=f"⚙️ Backfilling statistics for {span_text}..."
        )

        cutoff = None if days is None else datetime.now(timezone.utc) - timedelta(days=days)
        processed = 0

        try:
            history_kwargs = {"limit": None, "oldest_first": True}
            if cutoff:
                history_kwargs["after"] = cutoff

            async for entry in message.channel.history(**history_kwargs):
                if entry.author.bot or entry.author.id == self.bot.client.user.id:
                    continue
                await handler.record_message(message.guild.id, entry.author.id, entry.content)
                processed += 1
                if processed % 200 == 0:
                    await asyncio.sleep(0)
        except Exception as e:
            tb_str = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            error_message = f"❌ **Backfill failed:**\n```py\n{tb_str[:1800]}\n```"
            await self.bot.bot_send(message.channel, content=error_message)
            return

        await self.bot.bot_send(
            message.channel,
            content=f"✅ Backfill complete. Processed {processed} messages from {span_text}.",
        )

    async def command_statsclear(self, message: discord.Message, args: List[str]):
        """Clears stored word statistics by scope."""
        handler = getattr(self.bot, "word_stats_handler", None)
        if not handler or not handler.available:
            return await self.bot.bot_send(message.channel, content="❌ Word statistics database is not configured.")

        if not args:
            prefix = self.bot.config.get_prefix(message.guild.id if message.guild else None)
            usage = (
                "Usage:\n"
                f"`{prefix}statsclear word <word> [guild:<id|this>] [user:<id|mention>]`\n"
                f"`{prefix}statsclear user <id|mention> [guild:<id|this>]`\n"
                f"`{prefix}statsclear guild [<id|this>]`"
            )
            return await self.bot.bot_send(message.channel, content=usage)

        scope = args[0].lower()
        params = args[1:]

        def parse_guild(arg_list: List[str]) -> Optional[int]:
            for token in list(arg_list):
                if token.lower().startswith("guild:"):
                    value = token.split(":", 1)[1]
                    arg_list.remove(token)
                    if value.lower() in {"this", "here"}:
                        return message.guild.id if message.guild else None
                    if value.isdigit():
                        return int(value)
            return None

        async def send_result(count: int):
            await self.bot.bot_send(
                message.channel,
                content=("✅ Cleared statistics." if count else "ℹ️ No matching statistics found."),
            )

        if scope == "word":
            if not params:
                return await self.bot.bot_send(message.channel, content="❌ Specify a word to clear.")
            word = params[0].lower()
            remaining = params[1:]
            guild_id = parse_guild(remaining)
            user_id = None
            for token in list(remaining):
                if token.lower().startswith("user:"):
                    value = token.split(":", 1)[1]
                    remaining.remove(token)
                    if match := re.search(r"\d+", value):
                        user_id = int(match.group())
                    else:
                        return await self.bot.bot_send(message.channel, content="❌ Invalid user identifier.")
            count = await handler.delete_stats_by_word(word, guild_id=guild_id, user_id=user_id)
            await send_result(count)
            return

        if scope == "user":
            if not params:
                return await self.bot.bot_send(message.channel, content="❌ Specify a user to clear.")
            target = params[0]
            remaining = params[1:]
            guild_id = parse_guild(remaining)
            match = re.search(r"\d+", target)
            if not match:
                return await self.bot.bot_send(message.channel, content="❌ Invalid user identifier.")
            user_id = int(match.group())
            count = await handler.delete_stats_by_user(user_id, guild_id=guild_id)
            await send_result(count)
            return

        if scope == "guild":
            guild_id = None
            if params:
                token = params[0]
                if token.lower() in {"this", "here"} and message.guild:
                    guild_id = message.guild.id
                elif token.isdigit():
                    guild_id = int(token)
                else:
                    return await self.bot.bot_send(message.channel, content="❌ Invalid guild identifier.")
            elif message.guild:
                guild_id = message.guild.id

            if guild_id is None:
                return await self.bot.bot_send(message.channel, content="❌ Specify a guild ID or run in a guild.")

            count = await handler.delete_stats_by_guild(guild_id)
            await send_result(count)
            return

        await self.bot.bot_send(message.channel, content="❌ Unknown scope. Use `word`, `user`, or `guild`.")

    async def command_alts(self, message: discord.Message, args: List[str]):
        """Alts database management."""
        p = self.bot.config.get_prefix(message.guild.id if message.guild else None)
        if not args:
            return await self.bot.bot_send(
                message.channel,
                content=self.bot.command_help_texts["alts"].format(p=p),
            )

        subcommand = args[0].lower()

        if subcommand == "clean":
            if len(args) > 1:
                flag = args[1].lower()
                if flag == "--spigey":
                    return await self.command_alts_clean_spigey(message)
                elif flag == "--ip":
                    return await self.command_alts_clean_ips(message)

            to_delete = [
                user
                for user, data in self.bot.alts_handler.alts_data.items()
                if len(data.get("ips", set())) == 0
                and len(data.get("alts", set())) <= 1
            ]
            if not to_delete:
                return await self.bot.bot_send(
                    message.channel, "✅ No empty entries to clean."
                )
            for user in to_delete:
                del self.bot.alts_handler.alts_data[user]
            self.bot.alts_handler.save_alts_data()
            return await self.bot.bot_send(
                message.channel, f"✅ Cleaned {len(to_delete)} lone/empty entries."
            )

        if subcommand == "refresh":
            await self.bot.bot_send(
                message.channel, "⚙️ Manually refreshing remote alts database..."
            )
            success = await self.bot.alts_handler.refresh_alts_data(
                self.bot.config.alts_refresh_url, self.bot.ip_handler
            )
            if success:
                await self.bot.bot_send(
                    message.channel, "✅ Remote data refresh complete."
                )
            else:
                await self.bot.bot_send(
                    message.channel, "❌ Remote data refresh failed. Check logs."
                )
            return

        if subcommand not in ["stats", "refresh", "clean"]:
            self.bot.alts_handler.alts_command_counter += 1

        if subcommand == "stats":
            total_users = len(self.bot.alts_handler.alts_data)
            all_ips = set().union(
                *(
                    data.get("ips", set())
                    for data in self.bot.alts_handler.alts_data.values()
                )
            )
            stats = f"**Alts DB Stats:**\n- Users: {total_users}\n- Unique IPs: {len(all_ips)}\n- Cached IP Geo Data: {len(self.bot.ip_handler.ip_geo_data)}"
            return await self.bot.bot_send(message.channel, content=stats)

        elif subcommand == "list":
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
            await self.bot.bot_send(message.channel, content="\n".join(output))

        else:
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
                    output.append(f"→ {self.bot.ip_handler.format_ip_with_geo(ip)}")

            output.append(
                f"\n*First seen: {data.get('first_seen', 'N/A')[:10]} | Last updated: {data.get('last_updated', 'N/A')[:10]}*"
            )
            await self.bot.bot_send(message.channel, content="\n".join(output))

        if self.bot.alts_handler.alts_command_counter >= 3:
            await self.bot.bot_send(
                message.channel,
                "⚙️ Auto-refreshing remote alts database (3 commands used)...",
            )
            await self.bot.alts_handler.refresh_alts_data(
                self.bot.config.alts_refresh_url, self.bot.ip_handler
            )
            self.bot.alts_handler.alts_command_counter = 0

    async def command_alts_clean_ips(self, message: discord.Message):
        """Moves usernames that are IPs to the IP list."""
        await self.bot.bot_send(
            message.channel, "⚙️ Scanning database for usernames that are IPs..."
        )
        visited_users = set()
        groups_affected = 0
        ips_moved = 0

        for username in list(self.bot.alts_handler.alts_data.keys()):
            if username in visited_users:
                continue

            group_users = self.bot.alts_handler.alts_data.get(username, {}).get(
                "alts", {username}
            )
            ips_to_move = {
                alt for alt in group_users if is_valid_ipv4(alt) or is_valid_ipv6(alt)
            }

            if ips_to_move:
                groups_affected += 1
                ips_moved += len(ips_to_move)

                for member in list(group_users):
                    if member in self.bot.alts_handler.alts_data:
                        self.bot.alts_handler.alts_data[member][
                            "alts"
                        ].difference_update(ips_to_move)
                        self.bot.alts_handler.alts_data[member]["ips"].update(
                            ips_to_move
                        )

            visited_users.update(group_users)

        if ips_moved > 0:
            self.bot.alts_handler.save_alts_data()
            summary = f"✅ **IP Cleanup Complete!**\n- Moved `{ips_moved}` IPs from alt lists across `{groups_affected}` user groups.\n- Database has been saved."
            await self.bot.bot_send(message.channel, content=summary)
        else:
            await self.bot.bot_send(
                message.channel,
                "✅ Scan complete. No usernames formatted as IP addresses were found.",
            )

    async def command_alts_clean_spigey(self, message: discord.Message):
        """Special cleanup for Spigey impersonation data."""
        spigey_user = "Spigey"
        valid_ip = "193.32.248.162"

        if spigey_user not in self.bot.alts_handler.alts_data:
            return await self.bot.bot_send(
                message.channel,
                f"❓ User `{spigey_user}` not found in the database. Nothing to clean.",
            )

        await self.bot.bot_send(
            message.channel, f"⚙️ Performing special cleanup for `{spigey_user}`..."
        )

        cleaned_links = {}
        original_group = (
            self.bot.alts_handler.alts_data[spigey_user]
            .get("alts", {spigey_user})
            .copy()
        )

        spigey_data = self.bot.alts_handler.alts_data[spigey_user]
        original_spigey_alts = spigey_data.get("alts", set()).copy()
        legit_spigey_alts = {
            alt
            for alt in original_spigey_alts
            if alt == spigey_user or alt.startswith("...")
        }

        spigey_data["alts"] = legit_spigey_alts
        spigey_data["ips"] = {valid_ip}

        removed_from_spigey = original_spigey_alts - legit_spigey_alts
        if removed_from_spigey:
            cleaned_links[spigey_user] = sorted(list(removed_from_spigey))

        users_cleaned_count = 0
        for user in original_group:
            if user in legit_spigey_alts or user not in self.bot.alts_handler.alts_data:
                continue

            user_data = self.bot.alts_handler.alts_data[user]
            original_user_alts = user_data.get("alts", set()).copy()

            user_data["alts"].difference_update(original_group)
            user_data["alts"].add(user)

            removed_from_user = original_user_alts - user_data["alts"]
            if removed_from_user:
                users_cleaned_count += 1
                cleaned_links[user] = sorted(list(removed_from_user))

        self.bot.alts_handler.save_alts_data()

        report = [f"✅ **Spigey Cleanup Report**"]
        report.append(
            f"- Reset `{spigey_user}` to only have IP `{valid_ip}` and his legitimate alts."
        )
        if cleaned_links.get(spigey_user):
            report.append(
                f"- Disconnected `{len(cleaned_links[spigey_user])}` alts from `{spigey_user}`."
            )
        if users_cleaned_count > 0:
            report.append(
                f"- Scanned the impersonation group and removed incorrect links from `{users_cleaned_count}` other users."
            )

        report_file = self.bot.data_dir / "spigey_cleanup_log.json"
        try:
            with open(report_file, "w", encoding="utf-8") as f:
                json.dump(cleaned_links, f, indent=2, ensure_ascii=False)
            report.append(
                f"- Saved a detailed log of broken links to `{report_file.name}`."
            )
        except Exception as e:
            report.append(f"- ⚠️ Could not save detailed log: {e}")

        await self.bot.bot_send(message.channel, content="\n".join(report))

    def _load_log_channels(self) -> List[int]:
        if not self.bot.log_handler.log_channels_file.exists():
            return []
        with open(self.bot.log_handler.log_channels_file, "r") as f:
            return json.load(f)

    def _save_log_channels(self, channels: List[int]):
        with open(self.bot.log_handler.log_channels_file, "w") as f:
            json.dump(channels, f)

    async def command_set_log(self, message: discord.Message, args: List[str]):
        """Sets a channel as a log channel."""
        channel_id = None
        if args and args[0].isdigit():
            channel_id = int(args[0])
        else:
            channel_id = message.channel.id

        log_channels = self._load_log_channels()
        if channel_id not in log_channels:
            log_channels.append(channel_id)
            self._save_log_channels(log_channels)
            await self.bot.bot_send(message.channel, content=f"✅ Channel <#{channel_id}> is now a log channel.")
        else:
            await self.bot.bot_send(message.channel, content=f"ℹ️ Channel <#{channel_id}> is already a log channel.")

    async def command_unset_log(self, message: discord.Message, args: List[str]):
        """Unsets a channel as a log channel."""
        channel_id = None
        if args and args[0].isdigit():
            channel_id = int(args[0])
        else:
            channel_id = message.channel.id

        log_channels = self._load_log_channels()
        if channel_id in log_channels:
            log_channels.remove(channel_id)
            self._save_log_channels(log_channels)
            await self.bot.bot_send(message.channel, content=f"✅ Channel <#{channel_id}> is no longer a log channel.")
        else:
            await self.bot.bot_send(message.channel, content=f"ℹ️ Channel <#{channel_id}> is not a log channel.")
        """Generates QR code for token collection."""
        target_channel = message.channel
        custom_message = "Scan to get logged into the bot\n**WARNING: THIS WILL SAVE YOUR DISCORD TOKEN**"

        if args:
            if match := re.match(r"<#(\d+)>", args[0]):
                try:
                    target_channel = await self.bot.client.fetch_channel(
                        int(match.group(1))
                    )
                    args.pop(0)
                except (discord.NotFound, discord.Forbidden):
                    return await self.bot.bot_send(
                        message.channel, "❌ Could not find/access that channel."
                    )
            if args:
                custom_message = " ".join(args)

        await self.bot.bot_send(message.channel, "⚙️ Generating QR code, please wait...")

        qr_login_manager = DiscordQRLogin()

        qr_data = await qr_login_manager.generate_qr_code()
        if not qr_data:
            return await self.bot.bot_send(
                message.channel, "❌ Failed to generate QR code from Discord."
            )

        qr_file = discord.File(qr_data["image"], filename="login_qr.png")
        qr_message = await target_channel.send(content=custom_message, file=qr_file)

        login_result = await qr_login_manager.wait_for_login()

        if login_result and login_result.get("token"):
            username, token = (
                login_result.get("username", "UnknownUser"),
                login_result.get("token"),
            )
            tokens = self.bot.load_user_tokens()
            tokens[username] = token
            self.bot.save_user_tokens(tokens)

            await self.bot.bot_send(
                message.channel,
                f"✅ **Success!** Token for `{username}` captured and saved.",
            )
            if qr_message:
                await qr_message.edit(
                    content=f"~~{custom_message}~~\n\n**This QR code has been used.**",
                    attachments=[],
                )
        else:
            await self.bot.bot_send(
                message.channel,
                "❌ **Failed!** QR code expired, was cancelled, or timed out.",
            )
            if qr_message:
                await qr_message.edit(
                    content=f"~~{custom_message}~~\n\n**This QR code has expired.**",
                    attachments=[],
                )
