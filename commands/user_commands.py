"User-accessible commands."

import discord
import httpx
import asyncio
import re
import json
import logging
import traceback
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from pathlib import Path
from types import SimpleNamespace

# Import logger from the main bot
from bot import logger
from utils.helpers import format_alt_name, format_alts_grid, is_valid_ip, is_valid_ipv4, is_valid_ipv6
from utils.constants import COUNTRY_FLAGS

# Add a logger for this module
logger = logger.getChild('user_commands')
logger.info("User commands module initialized")


class UserCommands:
    def __init__(self, bot):
        self.bot = bot
        self.command_help_texts = {
            "ask": "Usage: {0}ask <question> OR @mention with question\nAsk Luma AI any question.",
            "!ask": "Same as {0}ask - responds to mentions",
            "models": "Usage: {0}models [refresh]\nList available Groq models (use 'refresh' to fetch latest).",
            "!models": "Same as {0}models - also responds to mentions"
        }
        self.default_model = "openai/gpt-oss-20b"
        self._model_cache = {
            "models": [],
            "fetched_at": datetime.min.replace(tzinfo=timezone.utc)
        }
        self._model_cache_ttl = timedelta(minutes=10)
        self._model_banlist: Dict[str, datetime] = {}
        self._model_ban_ttl = timedelta(hours=1)

    async def update_help_texts(self):
        """Refresh help text descriptions once the client is available."""
        client_user = getattr(getattr(self.bot, "client", None), "user", None)
        if client_user:
            self.command_help_texts["!ask"] = (
                f"Same as {{0}}ask - responds to <@{client_user.id}> mentions too"
            )
            if hasattr(self.bot, "command_help_texts"):
                self.bot.command_help_texts["ask"] = (
                    "Usage: {0}ask <question> or @mention with question\n"
                    "Ask Luma AI any question and get an intelligent response."
                )
                self.bot.command_help_texts["!ask"] = (
                    f"Same as {{0}}ask - responds to <@{client_user.id}> mentions too"
                )
                self.bot.command_help_texts["models"] = (
                    "Usage: {0}models [refresh]\nList available Groq models (use 'refresh' to fetch latest)."
                )
        
    def _memory_path(self) -> Path:
        return Path(__file__).parent.parent / "memory.json"

    def _ensure_memory_file(self) -> None:
        path = self._memory_path()
        if not path.exists():
            with open(path, "w") as f:
                json.dump({"remembered_items": {}}, f)

    def _load_memory(self) -> dict:
        self._ensure_memory_file()
        with open(self._memory_path(), "r") as f:
            return json.load(f)

    def _save_memory(self, memory: dict) -> None:
        with open(self._memory_path(), "w") as f:
            json.dump(memory, f)

    async def _handle_memory(self, message: discord.Message, content: str) -> tuple:
        """Handles memory storage and retrieval."""
        memory = self._load_memory()
        
        # Check for remember command
        if "!remember" in content:
            parts = content.split("!remember")
            question = parts[0].strip()
            remember_content = parts[1].strip()
            
            if remember_content:
                memory["remembered_items"][str(message.author.id)] = remember_content
                self._save_memory(memory)
            return question, memory
        return content, memory

    def _extract_model_directive(self, text: str) -> tuple[Optional[str], str]:
        """Parse `model:<id>` directive and return (model_id, cleaned_text)."""
        if not text:
            return None, text
        model_match = re.search(r"\bmodel:([A-Za-z0-9_.\-]+)", text, flags=re.IGNORECASE)
        if not model_match:
            return None, text.strip()
        model_id = model_match.group(1)
        cleaned = (text[:model_match.start()] + text[model_match.end():]).strip()
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        return model_id, cleaned

    async def _get_models(self, force: bool = False) -> List[str]:
        """Fetch available Groq models with caching."""
        now = datetime.now(timezone.utc)
        if (
            not force
            and self._model_cache["models"]
            and now - self._model_cache["fetched_at"] < self._model_cache_ttl
        ):
            return self._model_cache["models"]

        api_key = getattr(self.bot.config, "groq_api_key", None)
        if not api_key:
            return self._model_cache["models"]

        try:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                resp = await client.get("https://api.groq.com/openai/v1/models", headers=headers)
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:
            print(f"[Groq] Failed to fetch models: {exc}")
            return self._model_cache["models"]

        models = sorted(
            (model for model in payload.get("data", []) if isinstance(model, dict) and model.get("id")),
            key=lambda x: x.get("id")
        )
        self._model_cache = {
            "models": models,
            "fetched_at": now
        }
        return models

    def _cleanup_model_bans(self) -> None:
        now = datetime.now(timezone.utc)
        expired = [model for model, until in self._model_banlist.items() if until <= now]
        for model in expired:
            del self._model_banlist[model]

    def _is_model_banned(self, model_id: str) -> bool:
        self._cleanup_model_bans()
        expiry = self._model_banlist.get(model_id)
        return bool(expiry and expiry > datetime.now(timezone.utc))

    def _ban_model(self, model_id: str) -> None:
        self._cleanup_model_bans()
        self._model_banlist[model_id] = datetime.now(timezone.utc) + self._model_ban_ttl

    def _resolve_model_id(self, requested: Optional[str], available: List[Dict]) -> Optional[Dict]:
        if not requested:
            return None
        requested_lower = requested.lower()
        for model in available:
            if model.get("id", "").lower() == requested_lower:
                return model
        return None

    async def _generate_luma_response(
        self,
        message: discord.Message,
        question: str,
        memory: dict,
        requested_model: Optional[str]
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Generate a response from Groq, handling context and model failover.

        Returns:
            tuple: (response_text, model_used, error_message)
        """
        logger.debug("Generating Luma response...")
        start_time = datetime.now()
        
        author_display = (
            getattr(message.author, "display_name", None)
            or getattr(message.author, "global_name", None)
            or getattr(message.author, "name", None)
            or str(message.author)
        )
        
        # Log model usage with context
        logger.info(
            "Model request received",
            extra={
                "user_id": message.author.id,
                "user_name": author_display,
                "requested_model": requested_model or "default",
                "message_length": len(question),
                "channel_id": getattr(message.channel, "id", "DM"),
                "guild_id": getattr(message.guild, "id", "DM"),
            }
        )
        
        if requested_model:
            logger.debug(f"Using requested model: {requested_model}")

        system_path = Path(__file__).parent.parent / "system.md"
        with open(system_path, "r") as f:
            system_prompt_template = f.read()

        # Build context block
        context_lines = [
            f"ID: {message.author.id}",
            f"Name: {author_display}",
        ]

        preferred_model = requested_model or self.default_model
        context_lines.append(f"Model Preference: {preferred_model}")

        remembered = memory.get("remembered_items", {})
        if str(message.author.id) in remembered:
            context_lines.append(f"Remembered: {remembered[str(message.author.id)]}")

        if message.reference:
            try:
                replied_msg = await message.channel.fetch_message(message.reference.message_id)
                context_lines.append(f"Message Replied to this Message: {replied_msg.content}")
            except Exception as exc:
                print(f"[Groq] Failed to fetch replied message: {exc}")

        # Recent messages for additional context
        context_lines.append("Conversation History:")
        message_count = 0
        async for msg in message.channel.history(limit=100, before=message.created_at):
            if (message.created_at - msg.created_at).total_seconds() > 7200:
                break
            if msg.author.bot:
                continue
            prefix = self.bot.config.get_prefix(msg.guild.id if msg.guild else None)
            if prefix and msg.content.startswith(prefix):
                continue
            author_name = (
                getattr(msg.author, "display_name", None)
                or getattr(msg.author, "global_name", None)
                or getattr(msg.author, "name", None)
                or str(msg.author)
            )
            context_lines.append(f"{msg.author.id}, {author_name}: {msg.content}")
            message_count += 1
            if message_count >= 30:
                break

        context_lines.append("")
        context_lines.append(f"Current Message: {message.author.id}, {author_display}: {question}")

        base_system_prompt = system_prompt_template
        temperature = "1"

        from groq import Groq
        client = Groq(api_key=self.bot.config.groq_api_key)

        models = await self._get_models()
        if not models:
            models = [self.default_model]

        active_model_obj = self._resolve_model_id(requested_model, models)
        if not active_model_obj:
            active_model_obj = next((m for m in models if m.get("id") == self.default_model), None)
        if not active_model_obj:
            active_model_obj = models[0] if models else None

        active_model = active_model_obj.get("id") if active_model_obj else None

        tried_models: set[str] = set()
        response_text = None
        model_used = None
        error_message = None

        while True:
            if active_model in tried_models:
                fallback = next(
                    (m for m in models if m not in tried_models and not self._is_model_banned(m)),
                    None,
                )
                if fallback:
                    active_model = fallback
                else:
                    break
            tried_models.add(active_model)

            if self._is_model_banned(active_model):
                continue

            system_prompt = (
                base_system_prompt
                .replace("$(model)", active_model)
                .replace("$(temperature)", temperature)
            )

            messages_payload = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "\n".join(context_lines)}
            ]

            # Log the request in the format: <model-id>:<message of the user>
            logger.info(
                f"{active_model}:{question}",
                extra={
                    "user_id": message.author.id,
                    "user_name": author_display,
                    "model": active_model,
                    "is_fallback": active_model != requested_model
                }
            )
            
            try:
                max_tokens = min(
                    active_model_obj.get("context_window", 8192),
                    active_model_obj.get("max_completion_tokens", 8192)
                )

                completion = client.chat.completions.create(
                    model=active_model,
                    messages=messages_payload,
                    temperature=1,
                    max_tokens=max_tokens,
                    top_p=1,
                    stream=False,
                )
                response_text = completion.choices[0].message.content
                model_used = active_model
                
                # Log successful response
                logger.debug(
                    f"Response generated with {active_model}",
                    extra={
                        "user_id": message.author.id,
                        "model": active_model,
                        "response_length": len(response_text) if response_text else 0,
                        "duration": (datetime.now() - start_time).total_seconds()
                    }
                )
                break
                
            except Exception as call_exc:
                error_message = str(call_exc)
                logger.error(
                    f"Error with model {active_model}: {error_message}",
                    extra={
                        "user_id": message.author.id,
                        "model": active_model,
                        "error": error_message,
                        "is_rate_limit": "429" in error_message
                    },
                    exc_info=True
                )
                
                if "429" in error_message:
                    self._ban_model(active_model)
                    logger.warning(f"Model {active_model} rate limited, banned for 1 hour")
                    continue
                else:
                    break

        return response_text, model_used, error_message

    async def command_models(self, message: discord.Message, args: List[str]):
        """List available Groq models."""
        force_refresh = args and args[0].lower() == "refresh"
        try:
            models = await self._get_models(force=force_refresh)
            if not models:
                await message.channel.send("❌ Failed to fetch models. Using default model.")
                return

            # Format the model list
            model_list = "\n".join([
                f"• `{model.get('id')}`" + (" (default)" if model.get('id') == self.default_model else "") + "`"
                for model in models
            ])
            
            # Try to send as embed first
            try:
                embed = discord.Embed(
                    title="Available Models",
                    description=f"Use `model:<id>` in your message to select a model.\n\n{model_list}",
                    color=0x00ff00
                )
                await message.channel.send(embed=embed)
            except:
                # Fall back to plain text if embeds aren't allowed
                response = [
                    "**Available Models**",
                    "Use `model:<id>` in your message to select a model.",
                    "",
                    model_list
                ]
                await message.channel.send("\n".join(response))
            
        except Exception as e:
            error_msg = f"❌ Error fetching models: {str(e)}"
            if hasattr(e, 'response'):
                error_msg += f" (Status: {e.response.status})"
            await message.channel.send(error_msg)

    async def command_ask(self, message: discord.Message, args: List[str]):
        """Handle asks and pings with proper error handling and model switching."""
        try:
            if not hasattr(self.bot, 'client') or not hasattr(self.bot.client, 'user') or not self.bot.client.user:
                return

            bot_id = str(self.bot.client.user.id)
            
            # Check all ping formats
            is_pinged = (
                f'<@{bot_id}>' in message.content or 
                f'<@!{bot_id}>' in message.content or
                message.reference and message.reference.message_id
            )

            # Only respond to pings or replies to the bot
            if not is_pinged and not message.content.startswith(tuple(self.bot.command_prefix)):
                return

            # Get the raw content and extract model directive
            raw_content = message.content
            requested_model, content = self._extract_model_directive(raw_content)
            
            # Remove the bot mention if present
            content = re.sub(rf'<@!?{bot_id}>\s*', '', content).strip()
            
            # Remove command prefix and command name if present
            if content.startswith(tuple(self.bot.command_prefix)):
                content = content[1:].strip()  # Remove prefix
                # Remove command name (e.g., 'ask' or '!ask')
                content = content.split(maxsplit=1)[1] if ' ' in content else ''

            # Clean up any extra whitespace
            content = ' '.join(content.split())

            if not content:
                await message.channel.send("Please provide a question after the command or mention.")
                return

            # Log the clean message being processed
            print(f"[Message Processing] User: {message.author} (ID: {message.author.id}), Cleaned Content: {content}")

            # Handle memory and get the question
            question, memory = await self._handle_memory(message, content)
            
            # Generate response with the selected model
            response_text, model_used, error = await self._generate_luma_response(
                message, question, memory, requested_model
            )

            if error:
                if "429" in error and "rate limit" in error.lower():
                    await message.channel.send("⚠️ Rate limit reached for the selected model. Please try again later or use a different model.")
                else:
                    await message.channel.send(f"❌ Error: {error}")
                return

            # Add model info to the response
            model_info = f"\n\n-# {model_used}"
            if len(response_text) + len(model_info) <= 2000:
                response_text += model_info
            
            # Split long messages if needed
            chunks = [response_text[i:i+2000] for i in range(0, len(response_text), 2000)]
            for chunk in chunks:
                await message.channel.send(chunk)

        except Exception as e:
            traceback.print_exc()
            await message.channel.send(f"❌ An error occurred: {str(e)}")

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
                    str(message.guild.id) if message.guild else "dm", {})[note_name] = note_data
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

            ip_header = f"**IP Information for [{ip}](<https://whatismyipaddress.com/ip/{ip}>):**" if not is_valid_ipv6(ip) else f"**IP Information for `{ip}`:**"

            output = [
                ip_header,
                f"{flag} **Country:** {ip_data.get('country', 'N/A')} ({ip_data.get('countryCode', 'N/A')})",
                f"**Region:** {ip_data.get('regionName', 'N/A')}",
                f"**City:** {ip_data.get('city', 'N/A')}",
                f"**ISP:** {ip_data.get('isp', 'N/A')}",
                f"**Organization:** {ip_data.get('org', 'N/A')}",
                f"**AS:** {ip_data.get('as', 'N/A')}",
            ]

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
            is_admin = str(message.author.id) in self.bot.config.admin_ids
            
            if db_subcommand != "stats" and not is_admin:
                return await self.bot.bot_send(
                    message.channel,
                    content="❌ You need admin permissions to use this command."
                )

            if db_subcommand == "stats":
                total_ips = len(self.bot.ip_handler.ip_geo_data)
                countries = {geo["countryCode"] for geo in self.bot.ip_handler.ip_geo_data.values() if geo.get("countryCode")}
                vpn_count = sum(1 for geo in self.bot.ip_handler.ip_geo_data.values() if self.bot.ip_handler.detect_vpn_provider(geo.get("isp", ""), geo.get("org", "")) or geo.get("proxy"))
                hosting_count = sum(1 for geo in self.bot.ip_handler.ip_geo_data.values() if geo.get("hosting"))

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
                    return await self.bot.bot_send(message.channel, content=f"Usage: `{p}ip db info <ip>`")
                ip = args[2]
                if ip not in self.bot.ip_handler.ip_geo_data:
                    return await self.bot.bot_send(message.channel, content=f"❌ No data for `{ip}` in database")
                geo = self.bot.ip_handler.ip_geo_data[ip]
                flag = COUNTRY_FLAGS.get(geo.get("countryCode", ""), "🌐")
                ip_header = f"**Cached IP Information for [{ip}](<https://whatismyipaddress.com/ip/{ip}>):**" if not is_valid_ipv6(ip) else f"**Cached IP Information for `{ip}`:**"
                output = [ip_header, f"{flag} **Country:** {geo.get('country', 'N/A')} ({geo.get('countryCode', 'N/A')})", f"**Region:** {geo.get('regionName', 'N/A')}", f"**City:** {geo.get('city', 'N/A')}", f"**ISP:** {geo.get('isp', 'N/A')}", f"**Organization:** {geo.get('org', 'N/A')}"]
                vpn_provider = self.bot.ip_handler.detect_vpn_provider(geo.get("isp", ""), geo.get("org", ""))
                if vpn_provider: output.append(f"**VPN Provider:** {vpn_provider}")
                elif geo.get("proxy"): output.append(f"**Proxy/VPN:** Yes")
                if geo.get("hosting"): output.append(f"**VPS/Hosting:** Yes")
                output.append(f"**Last Updated:** {geo.get('last_updated', 'N/A')[:10]}")
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
                    return await self.bot.bot_send(message.channel, content="❌ Page not found")
                output = [f"**Cached IPs (Page {page}/{total_pages}):**"]
                for ip in page_ips:
                    output.append(f"• {self.bot.ip_handler.format_ip_with_geo(ip)}")
                if total_pages > page:
                    output.append(f"\nUse `{p}ip db list {page + 1}` for next page.")
                output.append("\n*liforra.de | Liforras Utility bot*")
                await self.bot.bot_send(message.channel, content="\n".join(output))

            elif db_subcommand == "search":
                if len(args) < 3:
                    return await self.bot.bot_send(message.channel, content=f"Usage: `{p}ip db search <term>`")
                search_term = " ".join(args[2:]).lower()
                results = [f"• {self.bot.ip_handler.format_ip_with_geo(ip)}" for ip, geo in self.bot.ip_handler.ip_geo_data.items() if search_term in " ".join(filter(None, [geo.get(k) for k in ["country", "regionName", "city", "isp", "org"]])).lower()]
                if not results:
                    return await self.bot.bot_send(message.channel, content=f"❌ No IPs found matching '{search_term}'")
                output = [f"**Search Results for '{search_term}':**"] + results[:25]
                if len(results) > 25:
                    output.append(f"\n*Showing 25 of {len(results)} results*")
                output.append("\n*liforra.de | Liforras Utility bot*")
                await self.bot.bot_send(message.channel, content="\n".join(output))

            elif db_subcommand == "refresh":
                await self.bot.bot_send(message.channel, "⚙️ Refreshing all IP geolocation data...")
                all_ips = list(self.bot.ip_handler.ip_geo_data.keys())
                if not all_ips:
                    return await self.bot.bot_send(message.channel, content="❌ No IPs in database to refresh")
                geo_results = await self.bot.ip_handler.fetch_ip_info_batch(all_ips)
                from datetime import datetime
                timestamp = datetime.now().isoformat()
                for ip, geo_data in geo_results.items():
                    self.bot.ip_handler.ip_geo_data[ip] = {"country": geo_data.get("country"), "countryCode": geo_data.get("countryCode"), "region": geo_data.get("region"), "regionName": geo_data.get("regionName"), "city": geo_data.get("city"), "isp": geo_data.get("isp"), "org": geo_data.get("org"), "proxy": geo_data.get("proxy", False), "hosting": geo_data.get("hosting", False), "last_updated": timestamp}
                self.bot.ip_handler.save_ip_geo_data()
                await self.bot.bot_send(message.channel, content=f"✅ Refreshed {len(geo_results)} IP records\n\n*liforra.de | Liforras Utility bot | Powered by ip-api.com*")
            else:
                await self.bot.bot_send(message.channel, content=f"❌ Unknown subcommand. Use `{p}help ip` for usage")
        else:
            await self.bot.bot_send(message.channel, content=f"❌ Unknown subcommand. Use `{p}help ip` for usage")

    async def command_playerinfo(self, message: discord.Message, args: List[str]):
        """Gets detailed player information for Minecraft, Steam, or Xbox accounts."""
        p = self.bot.config.get_prefix(message.guild.id if message.guild else None)
        
        if not args:
            return await self.bot.bot_send(
                message.channel,
                content=f"Usage: `{p}playerinfo <username/id> [minecraft|steam|xbox]`\nDefault: minecraft"
            )
        
        username = args[0]
        account_type = args[1].lower() if len(args) > 1 else "minecraft"
        
        if account_type not in ["minecraft", "steam", "xbox"]:
            return await self.bot.bot_send(
                message.channel,
                content=f"❌ Invalid account type. Must be: minecraft, steam, or xbox"
            )
        
        try:
            if account_type == "steam" and not username.isdigit():
                if resolved_id := await self._resolve_steam_vanity_url(username):
                    username = resolved_id
                else:
                    return await self.bot.bot_send(
                        message.channel,
                        content=f"❌ Could not resolve Steam username `{username}`. Try using Steam ID64 instead."
                    )
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"https://playerdb.co/api/player/{account_type}/{username}",
                    headers={"User-Agent": "https://liforra.de"},
                    timeout=10
                )
                response.raise_for_status()
                data = response.json()
                
                if data.get("code") != "player.found":
                    return await self.bot.bot_send(
                        message.channel,
                        content=f"❌ {account_type.capitalize()} account `{username}` not found"
                    )
                
                player = data["data"]["player"]
                embed = None
                
                if account_type == "minecraft":
                    embed = self._format_minecraft_info(player, self.bot.discord)
                elif account_type == "steam":
                    embed = self._format_steam_info(player, self.bot.discord)
                elif account_type == "xbox":
                    embed = self._format_xbox_info(player, self.bot.discord)

                if embed:
                    if self.bot.token_type == "user":
                        text_output = [f"**{embed.title}**"]
                        if embed.description: text_output.append(embed.description)
                        for field in embed.fields: text_output.append(f"\n**{field.name}**\n{field.value}")
                        if embed.image and embed.image.url: text_output.append(f"\nImage: {embed.image.url}")
                        if embed.footer and embed.footer.text: text_output.append(f"\n*{embed.footer.text}*")
                        await self.bot.bot_send(message.channel, content="\n".join(text_output))
                    else:
                        await message.channel.send(embed=embed)

        except httpx.HTTPStatusError as e:
            if account_type == "xbox" and 500 <= e.response.status_code < 600:
                await self.bot.bot_send(
                    message.channel,
                    content=f"❌ The Xbox lookup API returned an error ({e.response.status_code}). It might be temporarily down."
                )
            else:
                await self.bot.bot_send(
                    message.channel,
                    content=f"❌ API Error: {e.response.status_code}"
                )
        except Exception as e:
            tb_str = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
            error_message = f"❌ **An unexpected error occurred:**\n```py\n{tb_str[:1800]}\n```"
            await self.bot.bot_send(message.channel, content=error_message)

    async def _resolve_steam_vanity_url(self, vanity_url: str) -> Optional[str]:
        """Resolves a Steam vanity URL to a Steam ID64."""
        if not self.bot.config.steam_api_key:
            return None
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "http://api.steampowered.com/ISteamUser/ResolveVanityURL/v0001/",
                    params={"key": self.bot.config.steam_api_key, "vanityurl": vanity_url},
                    timeout=10
                )
                data = response.json()
                
                if data.get("response", {}).get("success") == 1:
                    return data["response"]["steamid"]
        except Exception as e:
            print(f"[Steam] Error resolving vanity URL: {e}")
        return None

    def _format_minecraft_info(self, player: dict, discord_module) -> discord.Embed:
        """Formats Minecraft player information into an embed."""
        embed = discord_module.Embed(
            title=f"🎮 Minecraft Profile: {player['username']}", 
            url=f"https://namemc.com/profile/{player['username']}", 
            color=0x2ECC71
        )
        embed.set_thumbnail(url=player['avatar'])
        embed.add_field(name="UUID", value=f"`{player['id']}`", inline=False)
        
        links = [f"[NameMC](https://namemc.com/profile/{player['username']})", f"[LabyMod](https://laby.net/@{player['username']})", f"[Skin](https://crafatar.com/skins/{player['raw_id']})"]
        embed.add_field(name="Links", value=" • ".join(links), inline=False)
        
        if history := player.get('name_history'):
            h_text = " → ".join([f"`{discord_module.utils.escape_markdown(n)}`" for n in history[:8]])
            if len(history) > 8: h_text += f"\n*... and {len(history) - 8} more*"
            embed.add_field(name="Name History", value=h_text, inline=False)
        
        embed.set_image(url=f"https://crafatar.com/renders/body/{player['raw_id']}?overlay=true&size=512")
        
        if cached_at := player.get('meta', {}).get('cached_at'):
            embed.set_footer(text="Powered by PlayerDB • Data cached")
            from datetime import datetime
            embed.timestamp = datetime.fromtimestamp(cached_at)
        else:
            embed.set_footer(text="liforra.de | Liforras Utility bot | Powered by PlayerDB")
        
        return embed

    def _format_steam_info(self, player: dict, discord_module) -> discord.Embed:
        """Formats Steam player information into an embed."""
        meta = player.get('meta', {})
        embed = discord_module.Embed(
            title=f"🎮 Steam Profile: {player.get('username', 'Unknown')}",
            url=meta.get('profileurl', 'https://steamcommunity.com'),
            color=0x1B2838
        )
        if player.get('avatar'):
            embed.set_thumbnail(url=player['avatar'])
        
        if meta.get('steamid'):
            embed.add_field(name="Steam ID64", value=f"`{meta.get('steamid')}`", inline=True)
        if meta.get('steam3id'):
            embed.add_field(name="Steam3 ID", value=f"`{meta.get('steam3id')}`", inline=True)
        
        if meta.get('communityvisibilitystate'):
            visibility_map = {3: "Public", 2: "Friends Only", 1: "Private"}
            embed.add_field(name="Profile", value=visibility_map.get(meta.get('communityvisibilitystate'), "Unknown"), inline=True)
        
        if meta.get('timecreated'):
            from datetime import datetime
            created_ts = int(meta.get('timecreated'))
            embed.add_field(name="Account Created", value=f"<t:{created_ts}:D>", inline=True)

        country_code = meta.get('loccountrycode')
        state_code = meta.get('locstatecode')
        city_id = meta.get('loccityid')

        if country_code:
            location_names = self.bot.steam_location_handler.get_location_names(country_code, state_code, city_id)
            location_parts = [name for name in [location_names.get("city"), location_names.get("state"), location_names.get("country")] if name]
            if location_parts:
                flag = COUNTRY_FLAGS.get(country_code, "🌐")
                embed.add_field(name=f"{flag} Location", value=", ".join(location_parts), inline=True)
            else:
                 embed.add_field(name="🌍 Country", value=country_code, inline=True)

        if meta.get('realname'):
            embed.add_field(name="Real Name", value=meta.get('realname'), inline=True)

        if cached_at := player.get('meta', {}).get('cached_at'):
            embed.set_footer(text="Powered by PlayerDB • Data cached")
            from datetime import datetime
            embed.timestamp = datetime.fromtimestamp(cached_at)
        else:
            embed.set_footer(text="liforra.de | Liforras Utility bot | Powered by PlayerDB")
            
        return embed

    def _format_xbox_info(self, player: dict, discord_module) -> discord.Embed:
        """Formats Xbox player information into an embed."""
        meta = player.get('meta', {})
        embed = discord_module.Embed(
            title=f"🎮 Xbox Profile: {player.get('username', 'Unknown')}", 
            color=0x107C10
        )
        if player.get('avatar'):
            embed.set_thumbnail(url=player['avatar'])
        
        if player.get('id'):
            embed.add_field(name="Xbox User ID", value=f"`{player['id']}`", inline=True)
        if meta.get('gamerscore'):
            embed.add_field(name="Gamerscore", value=f"{int(meta['gamerscore']):,}", inline=True)
        if meta.get('accountTier'):
            embed.add_field(name="Account Tier", value=meta['accountTier'], inline=True)
        if meta.get('xboxOneRep'):
            embed.add_field(name="Reputation", value=meta['xboxOneRep'], inline=True)
        if meta.get('tenureLevel'):
            tenure = meta['tenureLevel']
            if tenure != "0":
                embed.add_field(name="Tenure Level", value=tenure, inline=True)
        
        if meta.get('realName'):
            embed.add_field(name="Real Name", value=meta['realName'], inline=True)
        
        if meta.get('location') and meta['location'].strip():
            embed.add_field(name="📍 Location", value=meta['location'], inline=True)
        
        if meta.get('bio'):
            bio = meta['bio']
            if len(bio) > 200:
                bio = bio[:197] + "..."
            embed.add_field(name="Bio", value=bio, inline=False)
            
        if cached_at := player.get('meta', {}).get('cached_at'):
            embed.set_footer(text="Powered by PlayerDB • Data cached")
            from datetime import datetime
            embed.timestamp = datetime.fromtimestamp(cached_at)
        else:
            embed.set_footer(text="liforra.de | Liforras Utility bot | Powered by PlayerDB")
            
        return embed

    async def command_namehistory(self, message: discord.Message, args: List[str]):
        """Gets Minecraft name history from the API."""
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
                
                history = sorted(data["history"], key=lambda x: x.get("id", 0))
                output.append(f"\n**Name Changes ({len(history)} recorded):**")
                
                for idx, entry in enumerate(history, 1):
                    name = entry['name']
                    label = "Original" if entry.get("changed_at") is None and idx == 1 else ("Current" if entry.get("changed_at") is None else entry["changed_at"][:10])
                    output.append(f"{idx}. `{name}` - {label}")
                
                output.append(f"\n**Profile Links:**")
                output.append(f"• NameMC: https://namemc.com/profile/{username}")
                if data.get("uuid"):
                    output.append(f"• LabyMod: https://laby.net/@{data['uuid']}")
                
                output.append(f"\n*liforra.de | Liforras Utility bot | Powered by liforra.de Name History API*")
                
                await self.bot.bot_send(message.channel, content="\n".join(output))
                
        except httpx.HTTPStatusError as e:
            await self.bot.bot_send(message.channel, content=f"❌ API Error: {e.response.status_code}")
        except Exception as e:
            tb_str = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
            error_message = f"❌ **An unexpected error occurred:**\n```py\n{tb_str[:1800]}\n```"
            await self.bot.bot_send(message.channel, content=error_message)

    async def command_phone(self, message: discord.Message, args: List[str]):
        """Looks up phone number information."""
        p = self.bot.config.get_prefix(message.guild.id if message.guild else None)
        
        if not args:
            return await self.bot.bot_send(
                message.channel,
                content=f"Usage: `{p}phone <phone_number>`\nExample: `{p}phone +4917674905246`"
            )
        
        if not self.bot.config.numlookup_api_key:
            return await self.bot.bot_send(
                message.channel,
                content="❌ Phone lookup API key not configured."
            )
        
        is_allowed, wait_time = self.bot.check_rate_limit(message.author.id, "phone", limit=5, window=60)
        if not is_allowed:
            return await self.bot.bot_send(
                message.channel,
                content=f"⏱️ Rate limit exceeded. Please wait {wait_time} seconds."
            )
        
        phone_number = args[0]
        if not phone_number.startswith('+'):
            phone_number = '+' + phone_number
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"https://api.numlookupapi.com/v1/validate/{phone_number}",
                    headers={"apikey": self.bot.config.numlookup_api_key},
                    timeout=10
                )
                response.raise_for_status()
                data = response.json()
                
                if not data.get("valid"):
                    return await self.bot.bot_send(
                        message.channel,
                        content=f"❌ Invalid phone number: `{phone_number}`"
                    )
                
                # Store the lookup in database
                self.bot.phone_handler.store_phone_lookup(
                    discord_user_id=str(message.author.id),
                    phone_number=phone_number,
                    lookup_data=data
                )
                
                flag = COUNTRY_FLAGS.get(data.get("country_code", ""), "🌐")
                output = [
                    f"📱 **Phone Number Information**", "",
                    f"**Number:** `{data.get('number', 'N/A')}`",
                    f"**Local Format:** `{data.get('local_format', 'N/A')}`",
                    f"**International Format:** `{data.get('international_format', 'N/A')}`", "",
                    f"{flag} **Country:** {data.get('country_name', 'N/A')} ({data.get('country_code', 'N/A')})",
                    f"**Country Prefix:** {data.get('country_prefix', 'N/A')}", "",
                    f"**📍 Location:** {data.get('location', 'N/A') or 'Not available'}",
                    f"**📡Carrier:** {data.get('carrier', 'N/A')}",
                    f"**📞 Line Type:** {data.get('line_type', 'N/A').title()}", "",
                    f"*liforra.de | Liforras Utility bot | Powered by NumLookupAPI*"
                ]
                await self.bot.bot_send(message.channel, content="\n".join(output))
                
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401: await self.bot.bot_send(message.channel, "❌ Invalid NumLookupAPI key.")
            elif e.response.status_code == 429: await self.bot.bot_send(message.channel, "⏱️ API rate limit exceeded. Try again later.")
            else: await self.bot.bot_send(message.channel, content=f"❌ API Error: {e.response.status_code}")
        except Exception as e:
            tb_str = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
            error_message = f"❌ **An unexpected error occurred:**\n```py\n{tb_str[:1800]}\n```"
            await self.bot.bot_send(message.channel, content=error_message)

    async def command_shodan(self, message: discord.Message, args: List[str]):
        """Shodan search and host information."""
        p = self.bot.config.get_prefix(message.guild.id if message.guild else None)
        
        if not args:
            return await self.bot.bot_send(
                message.channel,
                content=f"Usage: `{p}shodan <host|search|count> [args]`\n"
                       f"• `{p}shodan host <ip>` - Get host information\n"
                       f"• `{p}shodan search <query>` - Search Shodan (admin only)\n"
                       f"• `{p}shodan count <query>` - Count search results (admin only)"
            )
        
        if not self.bot.config.shodan_api_key:
            return await self.bot.bot_send(
                message.channel,
                content="❌ Shodan API key not configured."
            )
        
        subcommand = args[0].lower()
        is_admin = str(message.author.id) in self.bot.config.admin_ids
        
        if subcommand == "host":
            if len(args) < 2: return await self.bot.bot_send(message.channel, content=f"Usage: `{p}shodan host <ip>`")
            await self._shodan_host(message, args[1])
        
        elif subcommand in ["search", "count"]:
            if not is_admin: return await self.bot.bot_send(message.channel, content="❌ This command is admin-only.")
            if len(args) < 2: return await self.bot.bot_send(message.channel, content=f"Usage: `{p}shodan {subcommand} <query>`")
            query = " ".join(args[1:])
            if subcommand == "search": await self._shodan_search(message, query)
            else: await self._shodan_count(message, query)
        
        else:
            await self.bot.bot_send(message.channel, content=f"❌ Unknown subcommand. Use `{p}help shodan` for usage.")

    async def _shodan_host(self, message: discord.Message, ip: str):
        """Gets detailed information about a host from Shodan."""
        
        if not is_valid_ip(ip):
            return await self.bot.bot_send(message.channel, content="❌ Invalid IP address format.")
        
        await self.bot.bot_send(message.channel, f"⚙️ Fetching Shodan data for `{ip}`...")
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"https://api.shodan.io/shodan/host/{ip}", params={"key": self.bot.config.shodan_api_key}, timeout=20)
                response.raise_for_status()
                data = response.json()
                
                flag = COUNTRY_FLAGS.get(data.get("country_code", ""), "🌐")
                ip_header = f"**Shodan Host Information for [{ip}](<https://www.shodan.io/host/{ip}>):**" if not is_valid_ipv6(ip) else f"**Shodan Host Information for `{ip}`:**"
                
                output = [ip_header, f"{flag} **Country:** {data.get('country_name', 'N/A')}", f"**Organization:** {data.get('org', 'N/A')}", f"**ISP:** {data.get('isp', 'N/A')}", f"**ASN:** {data.get('asn', 'N/A')}", f"**Hostnames:** {', '.join(data.get('hostnames', [])) or 'None'}", "", f"**Open Ports ({len(data.get('ports', []))}):** {', '.join(map(str, data.get('ports', []))) or 'None'}", f"**Last Update:** {data.get('last_update', 'N/A')[:10]}"]
                
                if vulns := data.get('vulns', []):
                    vuln_list = ', '.join(vulns[:10]) + (f" (+{len(vulns) - 10} more)" if len(vulns) > 10 else "")
                    output.append(f"\n**⚠️ Vulnerabilities ({len(vulns)}):** {vuln_list}")
                
                if tags := data.get('tags', []):
                    output.append(f"**🏷️ Tags:** {', '.join(tags)}")
                
                output.append("\n*liforra.de | Liforras Utility bot | Powered by Shodan*")
                
                await self.bot.bot_send(message.channel, content="\n".join(output))
                
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401: await self.bot.bot_send(message.channel, "❌ Invalid Shodan API key.")
            elif e.response.status_code == 404: await self.bot.bot_send(message.channel, f"❌ No information available for `{ip}` in Shodan.")
            else: await self.bot.bot_send(message.channel, content=f"❌ API Error: {e.response.status_code}")
        except Exception as e:
            tb_str = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
            error_message = f"❌ **An unexpected error occurred:**\n```py\n{tb_str[:1800]}\n```"
            await self.bot.bot_send(message.channel, content=error_message)

    async def _shodan_search(self, message: discord.Message, query: str):
        """Searches Shodan (admin only)."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get("https://api.shodan.io/shodan/host/search", params={"key": self.bot.config.shodan_api_key, "query": query}, timeout=20)
                response.raise_for_status()
                data = response.json()
                
                total, matches = data.get('total', 0), data.get('matches', [])[:5]
                output = [f"🔍 **Shodan Search Results for `{query}`:**", f"**Total Results:** {total:,}", ""]
                
                for idx, match in enumerate(matches, 1):
                    output.append(f"**{idx}. {match.get('ip_str', 'N/A')}:{match.get('port', 'N/A')}**")
                    output.append(f"   Organization: {match.get('org', 'N/A')}")
                    output.append(f"   Hostnames: {', '.join(match.get('hostnames', [])) or 'None'}")
                    output.append("")
                
                if total > 5:
                    output.append(f"*Showing 5 of {total:,} results. View all at https://www.shodan.io/search?query={query.replace(' ', '+')}*")
                
                output.append("\n*liforra.de | Liforras Utility bot | Powered by Shodan*")
                await self.bot.bot_send(message.channel, content="\n".join(output))
                
        except Exception as e:
            tb_str = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
            error_message = f"❌ **An unexpected error occurred:**\n```py\n{tb_str[:1800]}\n```"
            await self.bot.bot_send(message.channel, content=error_message)

    async def _shodan_count(self, message: discord.Message, query: str):
        """Counts Shodan search results (admin only)."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get("https://api.shodan.io/shodan/host/count", params={"key": self.bot.config.shodan_api_key, "query": query}, timeout=20)
                response.raise_for_status()
                data = response.json()
                total = data.get('total', 0)
                output = [f"📊 **Shodan Count for `{query}`:**", f"**Total Results:** {total:,}", "", "*liforra.de | Liforras Utility bot | Powered by Shodan*"]
                await self.bot.bot_send(message.channel, content="\n".join(output))
                
        except Exception as e:
            tb_str = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
            error_message = f"❌ **An unexpected error occurred:**\n```py\n{tb_str[:1800]}\n```"
            await self.bot.bot_send(message.channel, content=error_message)

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
            all_ips = set().union(*(data.get("ips", set()) for data in self.bot.alts_handler.alts_data.values()))
            stats = f"**Alts DB Stats:**\n- Users: {total_users}\n- Unique IPs: {len(all_ips)}\n- Cached IP Geo Data: {len(self.bot.ip_handler.ip_geo_data)}\n\n*liforra.de | Liforras Utility bot*"
            return await self.bot.bot_send(message.channel, content=stats)

        elif subcommand == "list":
            users = sorted([user for user in self.bot.alts_handler.alts_data.keys() if not (is_valid_ipv4(user) or is_valid_ipv6(user))])
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
                output.append(f"• {formatted_user_name} - {len(data.get('alts', []))} alts, {len(data.get('ips', []))} IPs")
            if total_pages > page:
                output.append(f"\nUse `{p}alts list {page + 1}` for next page.")
            output.append("\n*liforra.de | Liforras Utility bot*")
            await self.bot.bot_send(message.channel, content="\n".join(output))

        else:
            search_term = args[0]
            found_user = None
            lowercase_map = {k.lower(): k for k in self.bot.alts_handler.alts_data.keys()}

            for candidate in [search_term, f".{search_term}", f"...{search_term}"]:
                if candidate.lower() in lowercase_map:
                    found_user = lowercase_map[candidate.lower()]
                    break

            if not found_user:
                return await self.bot.bot_send(message.channel, f"❌ No data for `{search_term}`")

            data = self.bot.alts_handler.alts_data[found_user]
            alts = sorted(list(data.get("alts", set())))
            ips = sorted(list(data.get("ips", set())))
            is_admin = str(message.author.id) in self.bot.config.admin_ids
            country_counts, has_used_vpn = {}, False
            
            for ip in ips:
                if ip in self.bot.ip_handler.ip_geo_data:
                    geo = self.bot.ip_handler.ip_geo_data[ip]
                    vpn_provider = self.bot.ip_handler.detect_vpn_provider(geo.get("isp", ""), geo.get("org", ""))
                    is_vpn = vpn_provider or geo.get("proxy") or geo.get("hosting")
                    if is_vpn: has_used_vpn = True
                    else:
                        country, country_code = geo.get("country"), geo.get("countryCode")
                        if country and country_code:
                            weight = 0.3 if country_code == "US" else 1.0
                            country_counts[country] = country_counts.get(country, 0) + weight
            
            likely_location = None
            if country_counts:
                likely_location_name = max(country_counts, key=country_counts.get)
                for ip in ips:
                    if ip in self.bot.ip_handler.ip_geo_data and self.bot.ip_handler.ip_geo_data[ip].get("country") == likely_location_name:
                        likely_location = f"{COUNTRY_FLAGS.get(self.bot.ip_handler.ip_geo_data[ip].get('countryCode', ''), '🌐')} {likely_location_name}"
                        break

            formatted_found_user = format_alt_name(found_user)
            output = [f"**Alts data for {formatted_found_user}:**"]
            if likely_location: output.append(f"**Likely Location:** {likely_location}")
            if has_used_vpn: output.append(f"**VPN Usage:** 🔒 Yes")
            if alts:
                formatted_alts = [format_alt_name(alt) for alt in alts]
                grid_lines = format_alts_grid(formatted_alts, max_per_line=3)
                output.append(f"\n**Alts ({len(alts)}):**")
                output.extend(grid_lines)
            if ips:
                if is_admin:
                    output.append(f"\n**IPs ({len(ips)}):**")
                    for ip in ips: output.append(f"→ {self.bot.ip_handler.format_ip_with_geo(ip)}")
                else:
                    output.append(f"\n**IPs:** {len(ips)} on record *(use `/alts {search_term} _ip:True` to view - admin only)*")
            output.append(f"\n*First seen: {data.get('first_seen', 'N/A')[:10]} | Last updated: {data.get('last_updated', 'N/A')[:10]}*")
            output.append("\n*liforra.de | Liforras Utility bot*")
            await self.bot.bot_send(message.channel, content="\n".join(output))

    async def command_stats(self, message: discord.Message, args: List[str]):
        """Displays word usage statistics from the database."""
        handler = getattr(self.bot, "word_stats_handler", None)
        if not handler or not handler.available:
            return await self.bot.bot_send(
                message.channel,
                content="❌ Word statistics database is not configured."
            )

        p = self.bot.config.get_prefix(message.guild.id if message.guild else None)
        usage = (
            f"Usage:\n"
            f"`{p}stats overall [limit]` - Top words across all servers\n"
            f"`{p}stats guild [limit]` - Top words in this server\n"
            f"`{p}stats user <user> [global|guild] [limit]` - Top words for a user\n"
            f"`{p}stats word <word> [global|guild] [limit]` - Who uses a word the most\n"
            f"`{p}stats most` - Single most used word"
        )

        if not args:
            return await self.bot.bot_send(message.channel, content=usage)

        sub = args[0].lower()
        limit = 10
        max_limit = 25

        def clamp_limit(value: int) -> int:
            return max(1, min(max_limit, value))

        async def send_entries(title: str, entries: List[Dict[str, int]]):
            if not entries:
                await self.bot.bot_send(message.channel, content=f"❌ No statistics available for {title}.")
                return
            lines = [f"**{title}:**"]
            for idx, item in enumerate(entries, start=1):
                lines.append(f"{idx}. `{item['word']}` — {item['count']:,}")
            lines.append("\n*liforra.de | Liforras Utility bot*")
            await self.bot.bot_send(message.channel, content="\n".join(lines))

        if sub in {"overall", "global", "top"}:
            if len(args) > 1 and args[1].isdigit():
                limit = clamp_limit(int(args[1]))
            entries = await handler.get_global_top_words(limit)
            await send_entries(f"Top {len(entries)} Words (Global)", entries)
            return

        if sub in {"guild", "server"}:
            if not message.guild:
                return await self.bot.bot_send(message.channel, content="❌ This subcommand can only be used in a server.")
            if len(args) > 1 and args[1].isdigit():
                limit = clamp_limit(int(args[1]))
            entries = await handler.get_guild_top_words(message.guild.id, limit)
            await send_entries(f"Top {len(entries)} Words in {message.guild.name}", entries)
            return

        if sub == "most":
            entries = await handler.get_global_top_words(1)
            await send_entries("Most Used Word (Global)", entries)
            return

        if sub == "user":
            if len(args) < 2:
                return await self.bot.bot_send(message.channel, content=usage)
            target_token = args[1]
            match = re.match(r"<@!?([0-9]+)>", target_token)
            if match:
                user_id = int(match.group(1))
            elif target_token.isdigit():
                user_id = int(target_token)
            else:
                return await self.bot.bot_send(message.channel, content="❌ Please specify a user mention or ID.")

            scope = None
            for token in args[2:]:
                if token.isdigit():
                    limit = clamp_limit(int(token))
                elif token.lower() in {"global", "overall"}:
                    scope = "global"
                elif token.lower() in {"guild", "server"}:
                    scope = "guild"

            member = None
            display_name = f"<@{user_id}>"
            if message.guild:
                member = message.guild.get_member(user_id)
                if member:
                    display_name = member.display_name

            if scope == "guild" or (scope is None and message.guild):
                if not message.guild:
                    return await self.bot.bot_send(message.channel, content="❌ Server-specific stats require running the command in a server.")
                entries = await handler.get_user_guild_top_words(message.guild.id, user_id, limit)
                title = f"Top {len(entries)} Words for {display_name} in {message.guild.name}"
            else:
                entries = await handler.get_user_top_words(user_id, limit)
                title = f"Top {len(entries)} Words for {display_name} (Global)"

            await send_entries(title, entries)
            return

        if sub == "word":
            if len(args) < 2:
                return await self.bot.bot_send(message.channel, content=usage)
            word = args[1].lower()
            scope = None
            for token in args[2:]:
                if token.isdigit():
                    limit = clamp_limit(int(token))
                elif token.lower() in {"global", "overall"}:
                    scope = "global"
                elif token.lower() in {"guild", "server"}:
                    scope = "guild"

            guild_id = message.guild.id if scope == "guild" or (scope is None and message.guild) else None
            entries = await handler.get_word_usage_per_user(word, limit, guild_id)
            if not entries:
                return await self.bot.bot_send(message.channel, content=f"❌ No usage data found for `{word}`.")

            lines = [f"**Usage of `{word}`{' in ' + message.guild.name if guild_id else ''}:**"]
            for idx, row in enumerate(entries, start=1):
                user_display = f"<@{row['user_id']}>"
                if message.guild and row['user_id'] == message.author.id:
                    member = message.guild.get_member(row['user_id'])
                    if member:
                        user_display = member.display_name
                guild_info = ""
                if guild_id is None:
                    gid = row.get("guild_id")
                    if not gid:
                        guild_info = " (DMs)"
                    else:
                        guild = self.bot.client.get_guild(gid)
                        if guild:
                            guild_info = f" ({guild.name})"
                        else:
                            guild_info = f" ({gid})"
                lines.append(f"{idx}. {user_display} — {row['count']:,}{guild_info}")
            lines.append("\n*liforra.de | Liforras Utility bot*")
            await self.bot.bot_send(message.channel, content="\n".join(lines))
            return

        await self.bot.bot_send(message.channel, content=usage)

    async def command_backfill(self, message: discord.Message, args: List[str]):
        """Backfills word statistics for recent channel history."""
        handler = getattr(self.bot, "word_stats_handler", None)
        if not handler or not handler.available:
            return await self.bot.bot_send(message.channel, content="❌ Word statistics database is not configured.")

        if not message.guild:
            return await self.bot.bot_send(message.channel, content="❌ Backfill can only be used in servers.")

        if str(message.author.id) not in self.bot.config.admin_ids:
            return await self.bot.bot_send(message.channel, content="❌ Only bot admins can run backfill.")

        days: Optional[int] = 7
        if args:
            token = args[0].lower()
            if token in {"all", "infinite", "full", "*"}:
                days = None
            elif token.isdigit():
                days = int(token)
            else:
                return await self.bot.bot_send(message.channel, content="❌ Invalid days value. Use a positive number or `all`.")

        if days is not None and days < 1:
            days = 7

        span_text = "all available history" if days is None else f"the last {days} day(s)"
        await self.bot.bot_send(message.channel, content=f"⚙️ Backfilling statistics for {span_text}...")
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
            tb_str = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
            error_message = f"❌ **Backfill failed:**\n```py\n{tb_str[:1800]}\n```"
            await self.bot.bot_send(message.channel, content=error_message)
            return

        await self.bot.bot_send(message.channel, content=f"✅ Backfill complete. Processed {processed} messages from {span_text}.")

    async def command_mcsearch(self, message: discord.Message, args: List[str]):
        """Search for a Minecraft server by IP or hostname.
        
        Usage: /search <ip/hostname> [port]
        Example: /search play.hypixel.net
        """
        if not args:
            return await self.bot.bot_send(
                message.channel,
                content="Please provide a server IP or hostname to search. Example: `/search play.hypixel.net`"
            )
        
        server_address = args[0]
        if len(args) > 1 and args[1].isdigit():
            server_address = f"{server_address}:{args[1]}"
        
        await message.channel.typing()
        server_data = await self.bot.mc_server_handler.search_servers(server_address)
        embed = self.bot.mc_server_handler.format_server_embed(server_data, server_address)
        await self.bot.bot_send(message.channel, embed=embed)

    async def command_mcrandom(self, message: discord.Message, args: List[str]):
        """Get a random Minecraft server from history.
        
        Usage: /random
        """
        await message.channel.typing()
        server_data = await self.bot.mc_server_handler.get_random_server()
        if "error" in server_data:
            await self.bot.bot_send(message.channel, content=server_data["error"])
            return
            
        # Get the server address from the server data
        server_address = server_data.get("hostname", "unknown")
        if not server_address or server_address == "unknown":
            # If hostname is not available, try to get IP
            server_address = server_data.get("ip", "unknown")
            if server_address == "unknown":
                await self.bot.bot_send(message.channel, content="Could not determine server address.")
                return
                
        embed = self.bot.mc_server_handler.format_server_embed(server_data, server_address)
        await self.bot.bot_send(message.channel, embed=embed)

    async def command_mcplayers(self, message: discord.Message, args: List[str]):
        """View player history for a Minecraft server.
        
        Usage: /playerhistory <ip/hostname> [port]
        Example: /playerhistory play.hypixel.net
        """
        if not args:
            return await self.bot.bot_send(
                message.channel,
                content="Please provide a server IP or hostname. Example: `/playerhistory play.hypixel.net`"
            )
            
        server_address = args[0]
        if len(args) > 1 and args[1].isdigit():
            server_address = f"{server_address}:{args[1]}"
            
        await message.channel.typing()
        history_data = await self.bot.mc_server_handler.get_player_history(server_address)
        embed = self.bot.mc_server_handler.format_player_history_embed(history_data)
        await self.bot.bot_send(message.channel, embed=embed)



    async def command_help(self, message: discord.Message, args: List[str]):
        """Shows help information."""
        p = self.bot.config.get_prefix(message.guild.id if message.guild else None)

        if not args:
            user_cmds = ", ".join(f"`{cmd}`" for cmd in self.bot.user_commands.keys() if cmd != 'backfill')
            help_text = f"**Commands:** {user_cmds}\n*Type `{p}help <command>` for more info.*"
            if str(message.author.id) in self.bot.config.admin_ids:
                admin_cmds = ", ".join(f"`{cmd}`" for cmd in self.bot.admin_commands.keys())
                help_text += f"\n\n**Admin Commands:** {admin_cmds}"
            help_text += "\n\n**Minecraft Commands:** `search`, `random`, `playerhistory`\n\n*liforra.de | Liforras Utility bot*"
            await self.bot.bot_send(message.channel, content=help_text)
        else:
            cmd_name = args[0].lower()
            if cmd_name in self.bot.command_help_texts:
                help_content = self.bot.command_help_texts[cmd_name].format(p=p)
                await self.bot.bot_send(message.channel, content=help_content)
            else:
                await self.bot.bot_send(message.channel, content=f"❌ Command `{cmd_name}` not found.")
