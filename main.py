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
    
    # First, try to detect by token format
    # Bot tokens typically have 3 parts separated by dots
    # User tokens are longer and don't have this structure
    parts = token.split('.')
    
    if len(parts) == 3:
        # Likely a bot token, verify by attempting login
        try:
            import discord
            temp_client = discord.Client()
            try:
                await asyncio.wait_for(temp_client.login(token), timeout=10)
                is_bot = temp_client.user.bot if hasattr(temp_client.user, 'bot') else True
                await temp_client.close()
                return "bot" if is_bot else "user"
            except asyncio.TimeoutError:
                print(f"Timeout during bot token detection")
                if not temp_client.is_closed():
                    await temp_client.close()
            except Exception as e:
                print(f"Bot login attempt failed: {type(e).__name__}: {e}")
                if not temp_client.is_closed():
                    await temp_client.close()
        except ImportError:
            print("discord.py not installed, skipping bot token detection")
    
    # Try as user token
    try:
        import selfcord
        temp_client = selfcord.Client()
        try:
            await asyncio.wait_for(temp_client.login(token), timeout=10)
            await temp_client.close()
            return "user"
        except asyncio.TimeoutError:
            print(f"Timeout during user token detection")
            if not temp_client.is_closed():
                await temp_client.close()
        except Exception as e:
            print(f"User login attempt failed: {type(e).__name__}: {e}")
            if not temp_client.is_closed():
                await temp_client.close()
    except ImportError:
        print("selfcord.py not installed, skipping user token detection")
    
    return "unknown"


async def get_username_from_token(token: str, token_type: str = None) -> Optional[str]:
    """Gets username from a Discord token."""
    if token_type is None:
        token_type = await detect_token_type(token)
    
    if token_type == "bot":
        try:
            import discord
            temp_client = discord.Client()
        except ImportError:
            print("discord.py not installed")
            return None
    else:
        try:
            import selfcord
            temp_client = selfcord.Client()
        except ImportError:
            print("selfcord.py not installed")
            return None
    
    try:
        await asyncio.wait_for(temp_client.login(token), timeout=10)
        username = str(temp_client.user)
        await temp_client.close()
        return username
    except asyncio.TimeoutError:
        print(f"Timeout getting username for {token_type} token")
        return None
    except Exception as e:
        print(f"Error getting username: {type(e).__name__}: {e}")
        return None
    finally:
        if not temp_client.is_closed():
            try:
                await temp_client.close()
            except:
                pass


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
            print("Detecting token type...")
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

        for token_file in sorted(token_dir.iterdir()):
            if not token_file.is_file():
                continue

            token = token_file.read_text().strip()
            if not token:
                continue

            print(f"\nProcessing token from {token_file.name}...")
            token_type = await detect_token_type(token)
            
            if token_type == "unknown":
                print(f"!!! Could not determine token type for {token_file.name}, skipping. !!!")
                continue
            
            print(f"Token type: {token_type}")

            token_hash = hashlib.sha1(token.encode()).hexdigest()[:12]
            hashed_dir = data_dir / token_hash

            if hashed_dir.is_dir():
                bot_data_dir = hashed_dir
                print(f"Using existing data directory: {hashed_dir}")
            else:
                print(f"Getting username for token...")
                current_username = await get_username_from_token(token, token_type)
                if not current_username:
                    print(f"!!! Could not get username for {token_file.name}, skipping. !!!")
                    continue

                sanitized_current_user = sanitize_filename(current_username)
                print(f"Username: {current_username}")

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
            print("\nSaving updated user map...")
            data_dir.mkdir(exist_ok=True)
            user_map_path.write_text(json.dumps(user_map, indent=4))

    if not bots_to_run:
        return print("\nNo valid bot instances to run. Exiting.")

    print(f"\nStarting {len(bots_to_run)} bot instance(s)...\n")
    await asyncio.gather(*[bot.run() for bot in bots_to_run], return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down.")