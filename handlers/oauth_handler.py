"""OAuth2 authentication handler with PostgreSQL support."""

import json
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime


class OAuthHandler:
    def __init__(
        self,
        db_type: str = "json",
        db_url: str = "file:///home/liforra/bot-users.json",
        db_user: Optional[str] = None,
        db_password: Optional[str] = None,
    ):
        self.db_type = db_type
        self.db_url = db_url
        self.db_user = db_user
        self.db_password = db_password
        
        # PostgreSQL pool
        self.pg_pool = None
        
        # JSON file path
        self.oauth_file = None
        
        # Initialize based on database type
        if self.db_type == "postgres":
            self._init_postgres()
        else:
            self._init_json()
        
        # OAuth URL
        self.oauth_url = (
            "https://discord.com/oauth2/authorize?"
            "client_id=1426159248756441220&"
            "response_type=code&"
            "redirect_uri=https%3A%2F%2Fliforra.de%2Fbot&"
            "scope=identify+email+guilds"
        )

    def _init_postgres(self):
        """Initialize PostgreSQL connection."""
        try:
            from psycopg2 import pool
            
            self.pg_pool = pool.SimpleConnectionPool(
                1,
                10,
                dsn=self.db_url,
                user=self.db_user,
                password=self.db_password,
            )
            print(f"[OAuth] Connected to PostgreSQL database")
        except ImportError:
            print("[OAuth] ERROR: psycopg2 not installed. Install with: pip install psycopg2-binary")
            self.db_type = "json"
            self._init_json()
        except Exception as e:
            print(f"[OAuth] ERROR: Failed to connect to PostgreSQL: {e}")
            print("[OAuth] Falling back to JSON file storage")
            self.db_type = "json"
            self._init_json()

    def _init_json(self):
        """Initialize JSON file storage."""
        if self.db_url.startswith("file://"):
            self.oauth_file = Path(self.db_url.replace("file://", ""))
        else:
            self.oauth_file = Path(self.db_url)

    def is_user_authorized(self, user_id: str) -> bool:
        """Checks if a user has completed OAuth authorization."""
        if self.db_type == "postgres" and self.pg_pool:
            return self._is_user_authorized_postgres(user_id)
        else:
            return self._is_user_authorized_json(user_id)

    def _is_user_authorized_postgres(self, user_id: str) -> bool:
        """Checks authorization in PostgreSQL."""
        conn = None
        try:
            conn = self.pg_pool.getconn()
            cur = conn.cursor()
            
            # Check if user exists and has valid tokens
            cur.execute(
                """
                SELECT expires_at, refresh_token
                FROM bot_users
                WHERE discord_user_id = %s
                """,
                (str(user_id),)
            )
            
            result = cur.fetchone()
            if not result:
                return False
            
            expires_at, refresh_token = result
            
            # Check if we have a refresh token (means they've authorized)
            if not refresh_token:
                return False
            
            # Check if token is expired
            if expires_at:
                try:
                    expiry_time = datetime.fromisoformat(str(expires_at))
                    if datetime.now() < expiry_time:
                        return True
                    else:
                        print(f"[OAuth] Token expired for user {user_id}")
                        return False
                except:
                    pass
            
            return True
            
        except Exception as e:
            print(f"[OAuth] Error checking authorization in PostgreSQL: {e}")
            return False
        finally:
            if conn:
                self.pg_pool.putconn(conn)

    def _is_user_authorized_json(self, user_id: str) -> bool:
        """Checks authorization in JSON file."""
        if not self.oauth_file or not self.oauth_file.exists():
            return False
        
        try:
            with open(self.oauth_file, "r", encoding="utf-8") as f:
                oauth_data = json.load(f)
        except Exception as e:
            print(f"[OAuth] Error loading OAuth data: {e}")
            return False
        
        for entry in oauth_data:
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
        if self.db_type == "postgres" and self.pg_pool:
            return self._get_user_data_postgres(user_id)
        else:
            return self._get_user_data_json(user_id)

    def _get_user_data_postgres(self, user_id: str) -> Optional[Dict]:
        """Gets user data from PostgreSQL."""
        conn = None
        try:
            conn = self.pg_pool.getconn()
            cur = conn.cursor()
            
            # Get user data
            cur.execute(
                """
                SELECT username, avatar, code, access_token, refresh_token, expires_at
                FROM bot_users
                WHERE discord_user_id = %s
                """,
                (str(user_id),)
            )
            
            result = cur.fetchone()
            if not result:
                return None
            
            username, avatar, code, access_token, refresh_token, expires_at = result
            
            # Get email history
            cur.execute(
                """
                SELECT email, timestamp
                FROM bot_user_emails
                WHERE discord_user_id = %s
                ORDER BY timestamp DESC
                """,
                (str(user_id),)
            )
            
            emails = cur.fetchall()
            
            return {
                "userId": str(user_id),
                "username": username,
                "avatar": avatar,
                "code": code,
                "emails": [{"email": email, "timestamp": str(ts)} for email, ts in emails],
                "tokens": {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "expires_at": str(expires_at) if expires_at else None,
                }
            }
            
        except Exception as e:
            print(f"[OAuth] Error getting user data from PostgreSQL: {e}")
            return None
        finally:
            if conn:
                self.pg_pool.putconn(conn)

    def _get_user_data_json(self, user_id: str) -> Optional[Dict]:
        """Gets user data from JSON file."""
        if not self.oauth_file or not self.oauth_file.exists():
            return None
        
        try:
            with open(self.oauth_file, "r", encoding="utf-8") as f:
                oauth_data = json.load(f)
        except Exception as e:
            print(f"[OAuth] Error loading OAuth data: {e}")
            return None
        
        for entry in oauth_data:
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