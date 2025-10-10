"""Entry point for the Discord bot."""

import asyncio
import hashlib
import json
import os
import toml
from pathlib import Path
from typing import Optional

from bot import Bot
from utils.helpers import sanitize_filename


async def detect_token_type(token: str) -> str:
    """Detects if a token is a bot token or user token."""
    # Try bot token first
    try:
        import discord
        temp_client = discord.Client()
        try:
            await temp_client.login(token)
            is_bot = temp_client.user.bot if hasattr(temp_client.user, 'bot') else False
            await temp_client.close()
            return "bot" if is_bot else "user"
        except discord.LoginFailure:
            await temp_client.close()
            # If bot login fails, it might be a user token
            pass
        except Exception as e:
            if not temp_client.is_closed():
                await temp_client.close()
    except Exception:
        pass
    
    # Try user token
    try:
        import selfcord
        temp_client = selfcord.Client()
        try:
            await temp_client.login(token)
            await temp_client.close()
            return "user"
        except Exception:
            if not temp_client.is_closed():
                await temp_client.close()
    except Exception:
        pass
    
    return "unknown"


async def get_username_from_token(token: str, token_type: str = None) -> Optional[str]:
    """Gets username from a Discord token."""
    if token_type is None:
        token_type = await detect_token_type(token)
    
    if token_type == "bot":
        import discord
        temp_client = discord.Client()
    else:
        import selfcord
        temp_client = selfcord.Client()
    
    try:
        await temp_client.login(token)
        username = str(temp_client.user)
        await temp_client.close()
        return username
    except Exception:
        return None
    finally:
        if not temp_client.is_closed():
            await temp_client.close()


async def main():
    """Main entry point."""
    main_config_path = Path("config.toml")
    if not main_config_path.exists():
        return print("FATAL: Main 'config.toml' not found.")

    main_config = toml.load(main_config_path).get("general", {})
    mode = main_config.get("mode", "single")
    bots_to_run = []

    if mode == "single":
        print("Operating in SINGLE mode.")
        token = main_config.get("token", "")
        token_file = main_config.get("token-file", "")

        if not token and token_file and Path(token_file).exists():
            token = Path(token_file).read_text().strip()

        if token:
            token_type = await detect_token_type(token)
            if token_type == "unknown":
                print("FATAL: Could not determine token type or token is invalid.")
            else:
                print(f"Detected token type: {token_type}")
                bots_to_run.append(Bot(token=token, data_dir=Path("."), token_type=token_type))
        else:
            print("FATAL: No token found for single mode operation.")

    elif mode == "multi":
        print("Operating in MULTI mode.")
        token_dir = Path(main_config.get("token_directory", "./tokens"))
        data_dir = Path(main_config.get("data_directory", "./bot_data"))
        user_map_path = data_dir / "user_map.json"

        if not token_dir.is_dir():
            print(f"FATAL: Token directory '{token_dir}' not found. Creating.")
            token_dir.mkdir(parents=True, exist_ok=True)
            return

        try:
            user_map = (
                json.loads(user_map_path.read_text()) if user_map_path.exists() else {}
            )
        except json.JSONDecodeError:
            user_map = {}

        map_updated = False

        for token_file in token_dir.iterdir():
            if not token_file.is_file():
                continue

            token = token_file.read_text().strip()
            if not token:
                continue

            print(f"Processing token from {token_file.name}...")
            token_type = await detect_token_type(token)
            
            if token_type == "unknown":
                print(f"!!! Could not determine token type for {token_file.name}, skipping. !!!")
                continue
            
            print(f"Token type: {token_type}")

            token_hash = hashlib.sha1(token.encode()).hexdigest()[:12]
            hashed_dir = data_dir / token_hash

            if hashed_dir.is_dir():
                bot_data_dir = hashed_dir
            else:
                current_username = await get_username_from_token(token, token_type)
                if not current_username:
                    continue

                sanitized_current_user = sanitize_filename(current_username)

                stored_username = user_map.get(token_hash)
                if stored_username and stored_username != sanitized_current_user:
                    old_dir = data_dir / stored_username
                    new_dir = data_dir / sanitized_current_user

                    if old_dir.is_dir():
                        print(
                            f"Username changed for {token_hash}: '{stored_username}' -> '{sanitized_current_user}'. Renaming."
                        )
                        try:
                            os.rename(old_dir, new_dir)
                            bot_data_dir = new_dir
                        except OSError as e:
                            print(
                                f"!!! Could not rename dir for {token_hash}: {e}. !!!"
                            )
                            bot_data_dir = new_dir
                    else:
                        bot_data_dir = new_dir

                    user_map[token_hash] = sanitized_current_user
                    map_updated = True
                else:
                    bot_data_dir = data_dir / sanitized_current_user
                    if not stored_username:
                        print(
                            f"New user '{sanitized_current_user}' for hash {token_hash}. Creating mapping."
                        )
                        user_map[token_hash] = sanitized_current_user
                        map_updated = True

            if bot_data_dir:
                bots_to_run.append(Bot(token=token, data_dir=bot_data_dir, token_type=token_type))

        if map_updated:
            print("Saving updated user map...")
            data_dir.mkdir(exist_ok=True)
            user_map_path.write_text(json.dumps(user_map, indent=4))

    if not bots_to_run:
        return print("No valid bot instances to run. Exiting.")

    await asyncio.gather(*[bot.run() for bot in bots_to_run])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down.")