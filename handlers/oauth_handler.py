"""OAuth2 authentication handler."""

import json
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime


class OAuthHandler:
    def __init__(self, oauth_file: Path = Path("/home/liforra/bot-users.json")):
        self.oauth_file = oauth_file
        self.oauth_data = []
        self.load_oauth_data()
        
        # OAuth URL
        self.oauth_url = (
            "https://discord.com/oauth2/authorize?"
            "client_id=1426159248756441220&"
            "response_type=code&"
            "redirect_uri=https%3A%2F%2Fliforra.de%2Fbot&"
            "scope=identify+email+guilds"
        )

    def load_oauth_data(self):
        """Loads OAuth data from the JSON file."""
        if not self.oauth_file.exists():
            print(f"[OAuth] Warning: OAuth file not found at {self.oauth_file}")
            self.oauth_data = []
            return
        
        try:
            with open(self.oauth_file, "r", encoding="utf-8") as f:
                self.oauth_data = json.load(f)
            print(f"[OAuth] Loaded {len(self.oauth_data)} authorized users")
        except Exception as e:
            print(f"[OAuth] Error loading OAuth data: {e}")
            self.oauth_data = []

    def is_user_authorized(self, user_id: str) -> bool:
        """Checks if a user has completed OAuth authorization."""
        # Reload data to get fresh authorization status
        self.load_oauth_data()
        
        for entry in self.oauth_data:
            if entry.get("userId") == str(user_id):
                # Check if token is expired
                expires_at = entry.get("tokens", {}).get("expires_at")
                if expires_at:
                    try:
                        expiry_time = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                        if datetime.now(expiry_time.tzinfo) < expiry_time:
                            return True
                        else:
                            print(f"[OAuth] Token expired for user {user_id}")
                            return False
                    except:
                        pass
                return True
        return False

    def get_user_data(self, user_id: str) -> Optional[Dict]:
        """Gets the OAuth data for a specific user."""
        for entry in self.oauth_data:
            if entry.get("userId") == str(user_id):
                return entry
        return None

    def get_authorization_message(self, user_mention: str) -> str:
        """Generates the authorization required message."""
        return (
            f"🔒 **Authorization Required**\n\n"
            f"{user_mention}, you need to authorize this bot before using commands.\n\nThis is to prevent Rate Limits from our APIs.\n\n"
            f"**Click here to authorize:**\n"
            f"{self.oauth_url}\n\n"
            f"*After authorizing, try your command again.*"
        )