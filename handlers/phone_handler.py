"""Phone number lookup storage handler."""

import json
import uuid
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime


class PhoneHandler:
    def __init__(
        self,
        data_dir: Path,
        db_type: str = "json",
        db_url: Optional[str] = None,
        db_user: Optional[str] = None,
        db_password: Optional[str] = None,
    ):
        self.data_dir = data_dir
        self.db_type = db_type
        self.db_url = db_url
        self.db_user = db_user
        self.db_password = db_password
        
        self.pg_pool = None
        self.phone_file = data_dir / "phone_lookups.json"
        
        if self.db_type == "postgres":
            self._init_postgres()
        else:
            self._init_json()

    def _init_postgres(self):
        """Initialize PostgreSQL connection and create table if needed."""
        try:
            from psycopg2 import pool
            
            self.pg_pool = pool.SimpleConnectionPool(
                1, 10,
                dsn=self.db_url,
                user=self.db_user,
                password=self.db_password,
            )
            
            conn = self.pg_pool.getconn()
            try:
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS phone_lookups (
                        id VARCHAR(36) PRIMARY KEY,
                        discord_user_id VARCHAR(32) NOT NULL,
                        phone_number VARCHAR(32) NOT NULL,
                        valid BOOLEAN,
                        local_format VARCHAR(64),
                        international_format VARCHAR(64),
                        country_name VARCHAR(128),
                        country_code VARCHAR(8),
                        country_prefix VARCHAR(8),
                        location TEXT,
                        carrier VARCHAR(128),
                        line_type VARCHAR(32),
                        lookup_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_phone_user ON phone_lookups(discord_user_id);
                    CREATE INDEX IF NOT EXISTS idx_phone_number ON phone_lookups(phone_number);
                """)
                conn.commit()
                print(f"[Phone Handler] Connected to PostgreSQL database")
            finally:
                self.pg_pool.putconn(conn)
                
        except ImportError:
            print("[Phone Handler] ERROR: psycopg2 not installed. Falling back to JSON.")
            self.db_type = "json"
            self._init_json()
        except Exception as e:
            print(f"[Phone Handler] ERROR: Failed to connect to PostgreSQL: {e}")
            self.db_type = "json"
            self._init_json()

    def _init_json(self):
        """Initialize JSON file storage."""
        if not self.phone_file.exists():
            self.phone_file.write_text("[]")

    def store_phone_lookup(
        self,
        discord_user_id: str,
        phone_number: str,
        lookup_data: Dict
    ) -> str:
        """Stores a phone lookup result. Returns the lookup ID."""
        lookup_id = str(uuid.uuid4())
        
        if self.db_type == "postgres" and self.pg_pool:
            return self._store_postgres(lookup_id, discord_user_id, phone_number, lookup_data)
        else:
            return self._store_json(lookup_id, discord_user_id, phone_number, lookup_data)

    def _store_postgres(
        self,
        lookup_id: str,
        discord_user_id: str,
        phone_number: str,
        lookup_data: Dict
    ) -> str:
        """Stores phone lookup in PostgreSQL."""
        conn = None
        try:
            conn = self.pg_pool.getconn()
            cur = conn.cursor()
            
            cur.execute("""
                INSERT INTO phone_lookups (
                    id, discord_user_id, phone_number, valid,
                    local_format, international_format,
                    country_name, country_code, country_prefix,
                    location, carrier, line_type, lookup_timestamp
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
            """, (
                lookup_id,
                str(discord_user_id),
                phone_number,
                lookup_data.get("valid", False),
                lookup_data.get("local_format"),
                lookup_data.get("international_format"),
                lookup_data.get("country_name"),
                lookup_data.get("country_code"),
                lookup_data.get("country_prefix"),
                lookup_data.get("location"),
                lookup_data.get("carrier"),
                lookup_data.get("line_type"),
                datetime.now()
            ))
            
            conn.commit()
            return lookup_id
            
        except Exception as e:
            print(f"[Phone Handler] Error storing lookup in PostgreSQL: {e}")
            if conn:
                conn.rollback()
            return lookup_id
        finally:
            if conn:
                self.pg_pool.putconn(conn)

    def _store_json(
        self,
        lookup_id: str,
        discord_user_id: str,
        phone_number: str,
        lookup_data: Dict
    ) -> str:
        """Stores phone lookup in JSON file."""
        try:
            with open(self.phone_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            data.append({
                "id": lookup_id,
                "discord_user_id": str(discord_user_id),
                "phone_number": phone_number,
                "valid": lookup_data.get("valid", False),
                "local_format": lookup_data.get("local_format"),
                "international_format": lookup_data.get("international_format"),
                "country_name": lookup_data.get("country_name"),
                "country_code": lookup_data.get("country_code"),
                "country_prefix": lookup_data.get("country_prefix"),
                "location": lookup_data.get("location"),
                "carrier": lookup_data.get("carrier"),
                "line_type": lookup_data.get("line_type"),
                "lookup_timestamp": datetime.now().isoformat()
            })
            
            with open(self.phone_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                
            return lookup_id
            
        except Exception as e:
            print(f"[Phone Handler] Error storing lookup in JSON: {e}")
            return lookup_id