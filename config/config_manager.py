"""Configuration management for the bot."""

import toml
import ast
import json
import logging
from pathlib import Path
from typing import Optional, Any, Dict, List, Union

# Import logger from the main bot
from bot import logger

# Create a logger for this module
logger = logger.getChild('config')


class ConfigManager:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.config_file = data_dir / "config.toml"
        self.config_data = {}
        self.guild_configs = {}

        # Default values
        self.censor_config = []
        self.default_prefix = "€"
        self.default_websites = []
        self.default_friend_websites = []
        self.default_allow_commands = True
        self.default_prevent_deleting = True
        self.default_prevent_editing = True
        self.default_message_log = False
        self.default_attachment_log = False
        self.default_nodelete_download = False
        self.default_copyparty_url = ""
        self.default_allow_swears = True
        self.default_allow_slurs = False
        self.default_detect_ips = False
        self.default_clean_spigey = False

        self.alts_refresh_url = ""
        self.admin_ids = []
        self.discord_status_str = "online"
        self.configured_online_status = None
        self.match_status = False
        self.sync_channel_id = ""
        self.sync_mention_id = ""
        self.serpapi_key = ""
        
        # New API keys
        self.numlookup_api_key = ""
        self.shodan_api_key = ""
        self.steam_api_key = ""
        self.groq_api_key = ""
        
        # OAuth configuration
        self.oauth_db_type = "json"
        self.oauth_db_url = "file:///home/liforra/bot-users.json"
        self.oauth_db_user = None
        self.oauth_db_password = None
        self.oauth_client_id = None
        self.oauth_client_secret = None

        # Word statistics database configuration
        self.stats_db_type = "json"
        self.stats_db_url = ""
        self.stats_db_user = None
        self.stats_db_password = None

    def load_config(self):
        """Loads configuration from file."""
        if not self.config_file.exists():
            self.create_default_config()

        try:
            self.config_data = toml.load(self.config_file)
            general = self.config_data.get("general", {})

            self._fix_stringy_list(general, "censor-config")

            self.censor_config = general.get(
                "censor-config",
                [
                    "general.token",
                    "general.token-file",
                    "general.admin-ids",
                    "general.alts-refresh-url",
                    "general.oauth-db-url",
                    "general.oauth-db-user",
                    "general.oauth-db-password",
                    "general.oauth-client-id",
                    "general.oauth-client-secret",
                    "general.serpapi-key",
                    "general.numlookup-api-key",
                    "general.shodan-api-key",
                    "general.steam-api-key",
                    "general.groq-api-key",
                ],
            )
            self.alts_refresh_url = general.get("alts-refresh-url", "")
            self.admin_ids = [str(id) for id in general.get("admin-ids", [])]
            self.discord_status_str = general.get("discord-status", "online")
            self.match_status = general.get("match-status", False)
            self.default_prefix = general.get("prefix", "€")
            self.default_allow_commands = general.get("allow-commands", True)
            self.default_prevent_deleting = general.get("prevent-deleting", True)
            self.default_prevent_editing = general.get("prevent-editing", True)
            self.default_message_log = general.get("message-log", False)
            self.default_attachment_log = general.get(
                "attachment-log", general.get("image-log", False)
            )
            self.default_nodelete_download = general.get("nodelete-download", False)
            self.default_copyparty_url = general.get("copyparty", "")
            self.default_websites = general.get("websites", [])
            self.default_friend_websites = general.get("friend_websites", [])
            self.default_allow_swears = general.get("allow-swears", True)
            self.default_allow_slurs = general.get("allow-slurs", False)
            self.default_detect_ips = general.get("detect-ips", False)
            self.default_clean_spigey = general.get("clean-spigey", False)
            self.sync_channel_id = general.get("sync-channel", "")
            self.sync_mention_id = general.get("sync-mention-id", "")
            self.serpapi_key = general.get("serpapi-key", "")
            
            # New API keys
            self.numlookup_api_key = general.get("numlookup-api-key", "")
            self.shodan_api_key = general.get("shodan-api-key", "")
            self.steam_api_key = general.get("steam-api-key", "")
            self.groq_api_key = general.get("groq-api-key", "")
            
            # OAuth configuration
            self.oauth_db_type = general.get("oauth-db-type", "json")
            self.oauth_db_url = general.get(
                "oauth-db-url", "file:///home/liforra/bot-users.json"
            )
            self.oauth_db_user = general.get("oauth-db-user")
            self.oauth_db_password = general.get("oauth-db-password")
            self.oauth_client_id = general.get("oauth-client-id")
            self.oauth_client_secret = general.get("oauth-client-secret")

            stats_db_type = general.get("stats-db-type")
            stats_db_url = general.get("stats-db-url")
            stats_db_user = general.get("stats-db-user")
            stats_db_password = general.get("stats-db-password")

            self.stats_db_type = stats_db_type or self.oauth_db_type
            self.stats_db_url = stats_db_url or self.oauth_db_url
            self.stats_db_user = (
                stats_db_user if stats_db_user not in (None, "") else self.oauth_db_user
            )
            self.stats_db_password = (
                stats_db_password if stats_db_password not in (None, "") else self.oauth_db_password
            )

            if (self.stats_db_type != "postgres" or not self.stats_db_url) and Path("config.toml").exists():
                try:
                    root_conf = toml.load(Path("config.toml"))
                    root_general = root_conf.get("general", {})
                    root_stats_type = root_general.get("stats-db-type")
                    root_stats_url = root_general.get("stats-db-url")
                    root_stats_user = root_general.get("stats-db-user")
                    root_stats_password = root_general.get("stats-db-password")

                    if root_stats_url:
                        self.stats_db_type = root_stats_type or "postgres"
                        self.stats_db_url = root_stats_url
                        self.stats_db_user = root_stats_user if root_stats_user not in (None, "") else self.stats_db_user
                        self.stats_db_password = root_stats_password if root_stats_password not in (None, "") else self.stats_db_password
                    elif root_general.get("oauth-db-type") == "postgres" and root_general.get("oauth-db-url"):
                        self.stats_db_type = "postgres"
                        self.stats_db_url = root_general.get("oauth-db-url")
                        self.stats_db_user = root_general.get("oauth-db-user", self.stats_db_user)
                        self.stats_db_password = root_general.get("oauth-db-password", self.stats_db_password)
                except Exception as e:
                    print(f"[{self.data_dir.name}] Warning: failed to load root stats DB config: {e}")

            self.guild_configs = self.config_data.get("guild", {})

            print(f"[{self.data_dir.name}] Config loaded.")
        except Exception as e:
            print(f"[{self.data_dir.name}] Error loading config: {e}")

    def create_default_config(self):
        """Creates a default configuration file."""
        default_config = {
            "general": {
                "alts-refresh-url": "",
                "censor-config": [
                    "general.token",
                    "general.token-file",
                    "general.admin-ids",
                    "general.alts-refresh-url",
                    "general.oauth-db-url",
                    "general.oauth-db-user",
                    "general.oauth-db-password",
                    "general.oauth-client-id",
                    "general.oauth-client-secret",
                    "general.serpapi-key",
                    "general.numlookup-api-key",
                    "general.shodan-api-key",
                    "general.steam-api-key",
                    "general.groq-api-key",
                ],
                "discord-status": "online",
                "match-status": False,
                "admin-ids": [],
                "prefix": "€",
                "sync-channel": "",
                "sync-mention-id": "",
                "allow-commands": True,
                "prevent-deleting": True,
                "prevent-editing": True,
                "message-log": False,
                "attachment-log": False,
                "nodelete-download": False,
                "copyparty": "https://your.copyparty.url/",
                "websites": ["https://google.com"],
                "friend_websites": [],
                "allow-swears": True,
                "allow-slurs": False,
                "detect-ips": False,
                "clean-spigey": False,
                "serpapi-key": "",
                "numlookup-api-key": "",
                "shodan-api-key": "",
                "steam-api-key": "",
                "groq-api-key": "",
                "stats-db-type": "postgres",
                "stats-db-url": "",
                "stats-db-user": "",
                "stats-db-password": "",
                "oauth-db-type": "json",
                "oauth-db-url": "file:///home/liforra/bot-users.json",
                "oauth-db-user": "",
                "oauth-db-password": "",
                "oauth-client-id": "",
                "oauth-client-secret": "",
            },
            "guild": {},
        }
        with open(self.config_file, "w") as f:
            toml.dump(default_config, f)
        print(f"[{self.data_dir.name}] Created default config.")

    def _fix_stringy_list(self, config_dict, key):
        """Fixes lists that are stored as strings."""
        if key in config_dict and isinstance(config_dict[key], str):
            try:
                if isinstance(ast.literal_eval(config_dict[key]), list):
                    config_dict[key] = ast.literal_eval(config_dict[key])
            except:
                pass

    def get_guild_config(
        self,
        guild_id: Optional[int],
        setting: str,
        default_value: Any,
        user_id: Optional[int] = None,
        channel_id: Optional[int] = None,
    ) -> Any:
        """Gets a config value with guild/channel/user override support."""
        if guild_id is None:
            return self.config_data.get("general", {}).get(setting, default_value)

        guild_config = self.guild_configs.get(str(guild_id), {})
        value = guild_config.get(
            setting, self.config_data.get("general", {}).get(setting, default_value)
        )

        if (
            channel_id
            and (
                ch_override := guild_config.get("channel_overrides", {})
                .get(str(channel_id), {})
                .get(setting)
            )
            is not None
        ):
            value = ch_override

        if (
            user_id
            and (
                usr_override := guild_config.get("user_overrides", {})
                .get(str(user_id), {})
                .get(setting)
            )
            is not None
        ):
            value = usr_override

        return value

    def get_prefix(self, guild_id: Optional[int]) -> str:
        """Gets the command prefix for a guild."""
        return self.get_guild_config(guild_id, "prefix", self.default_prefix)

    def censor_recursive(self, path_prefix: str, data: Any) -> Any:
        """Recursively censors sensitive config values."""
        if path_prefix in self.censor_config:
            return "[CENSORED]"
        if isinstance(data, dict):
            return {
                key: self.censor_recursive(f"{path_prefix}.{key}", value)
                for key, value in data.items()
            }
        return data

    def get_attachment_log_setting(
        self,
        guild_id: Optional[int],
        user_id: Optional[int] = None,
        channel_id: Optional[int] = None,
    ) -> bool:
        """Gets the attachment logging setting."""
        val = self.get_guild_config(
            guild_id, "attachment-log", None, user_id, channel_id
        )
        return (
            val
            if val is not None
            else self.get_guild_config(
                guild_id, "image-log", self.default_attachment_log, user_id, channel_id
            )
        )

    def parse_value(self, value_str: str) -> Any:
        """Parses a string value to its proper type."""
        if value_str.lower() == "true":
            return True
        if value_str.lower() == "false":
            return False
        if value_str.isdigit():
            return int(value_str)
        try:
            return ast.literal_eval(value_str)
        except (ValueError, SyntaxError):
            return value_str