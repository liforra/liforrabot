import asyncio
import re
from collections import Counter
from typing import Dict, List, Optional


class WordStatsHandler:
    def __init__(
        self,
        db_type: str,
        db_url: Optional[str],
        db_user: Optional[str],
        db_password: Optional[str],
        existing_pool=None,
    ):
        self.db_type = db_type or ""
        self.db_url = db_url
        self.db_user = db_user
        self.db_password = db_password
        self.pg_pool = existing_pool
        self.available = False
        self._token_pattern = re.compile(r"[0-9a-zA-Z']+")

        if self.db_type == "postgres" and (self.db_url or self.pg_pool):
            if self.pg_pool:
                self.available = True
                self._ensure_schema()
            else:
                self._init_postgres()

    def _init_postgres(self):
        try:
            from psycopg2 import pool  # type: ignore
            self.pg_pool = pool.SimpleConnectionPool(1, 10, dsn=self.db_url, user=self.db_user, password=self.db_password)
            self._ensure_schema()
        except Exception as e:
            print(f"[WordStats] Error initializing PostgreSQL: {e}")
            self.available = False

    def _ensure_schema(self):
        if not self.pg_pool:
            return
        conn = None
        try:
            conn = self.pg_pool.getconn()
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS word_usage (
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    word TEXT NOT NULL,
                    count BIGINT NOT NULL,
                    PRIMARY KEY (guild_id, user_id, word)
                );
                CREATE INDEX IF NOT EXISTS idx_word_usage_word ON word_usage(word);
                CREATE INDEX IF NOT EXISTS idx_word_usage_user ON word_usage(user_id);
                CREATE INDEX IF NOT EXISTS idx_word_usage_guild ON word_usage(guild_id);
                """
            )
            conn.commit()
            self.available = True
        except Exception as e:
            if conn:
                conn.rollback()
            print(f"[WordStats] Error ensuring schema: {e}")
        finally:
            if conn:
                self.pg_pool.putconn(conn)

    async def record_message(self, guild_id: Optional[int], user_id: int, content: Optional[str]):
        if not self.available or not content:
            return
        words = self._token_pattern.findall(content.lower())
        if not words:
            return
        counts = Counter(words)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._store_counts, self._normalize_guild_id(guild_id), int(user_id), counts)

    def _store_counts(self, guild_id: int, user_id: int, counts: Counter):
        if not self.pg_pool:
            return
        conn = None
        try:
            conn = self.pg_pool.getconn()
            cur = conn.cursor()
            rows = [(guild_id, user_id, word, int(count)) for word, count in counts.items() if word]
            if not rows:
                return
            cur.executemany(
                """
                INSERT INTO word_usage (guild_id, user_id, word, count)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (guild_id, user_id, word)
                DO UPDATE SET count = word_usage.count + EXCLUDED.count
                """,
                rows,
            )
            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            print(f"[WordStats] Error storing counts: {e}")
        finally:
            if conn:
                self.pg_pool.putconn(conn)

    async def get_global_top_words(self, limit: int) -> List[Dict[str, int]]:
        query = "SELECT word, SUM(count) AS total FROM word_usage GROUP BY word ORDER BY total DESC LIMIT %s"
        rows = await self._fetch(query, (limit,))
        return [{"word": row[0], "count": int(row[1])} for row in rows]

    async def get_guild_top_words(self, guild_id: int, limit: int) -> List[Dict[str, int]]:
        query = "SELECT word, SUM(count) AS total FROM word_usage WHERE guild_id = %s GROUP BY word ORDER BY total DESC LIMIT %s"
        rows = await self._fetch(query, (self._normalize_guild_id(guild_id), limit))
        return [{"word": row[0], "count": int(row[1])} for row in rows]

    async def get_user_top_words(self, user_id: int, limit: int) -> List[Dict[str, int]]:
        query = "SELECT word, SUM(count) AS total FROM word_usage WHERE user_id = %s GROUP BY word ORDER BY total DESC LIMIT %s"
        rows = await self._fetch(query, (int(user_id), limit))
        return [{"word": row[0], "count": int(row[1])} for row in rows]

    async def get_user_guild_top_words(self, guild_id: int, user_id: int, limit: int) -> List[Dict[str, int]]:
        query = "SELECT word, SUM(count) AS total FROM word_usage WHERE guild_id = %s AND user_id = %s GROUP BY word ORDER BY total DESC LIMIT %s"
        rows = await self._fetch(query, (self._normalize_guild_id(guild_id), int(user_id), limit))
        return [{"word": row[0], "count": int(row[1])} for row in rows]

    async def get_word_usage_per_user(self, word: str, limit: int, guild_id: Optional[int] = None) -> List[Dict[str, int]]:
        if guild_id is None:
            query = "SELECT guild_id, user_id, SUM(count) AS total FROM word_usage WHERE word = %s GROUP BY guild_id, user_id ORDER BY total DESC LIMIT %s"
            rows = await self._fetch(query, (word, limit))
        else:
            query = "SELECT guild_id, user_id, SUM(count) AS total FROM word_usage WHERE word = %s AND guild_id = %s GROUP BY guild_id, user_id ORDER BY total DESC LIMIT %s"
            rows = await self._fetch(query, (word, self._normalize_guild_id(guild_id), limit))
        results: List[Dict[str, int]] = []
        for row in rows:
            gid = int(row[0])
            results.append({"guild_id": None if gid == 0 else gid, "user_id": int(row[1]), "count": int(row[2])})
        return results

    async def get_word_totals(self, word: str, guild_id: Optional[int] = None) -> int:
        if guild_id is None:
            query = "SELECT SUM(count) FROM word_usage WHERE word = %s"
            params = (word,)
        else:
            query = "SELECT SUM(count) FROM word_usage WHERE word = %s AND guild_id = %s"
            params = (word, self._normalize_guild_id(guild_id))
        rows = await self._fetch(query, params)
        if not rows or rows[0][0] is None:
            return 0
        return int(rows[0][0])

    async def _fetch(self, query: str, params: tuple) -> List[tuple]:
        if not self.available or not self.pg_pool:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._execute_fetch, query, params)

    def _execute_fetch(self, query: str, params: tuple) -> List[tuple]:
        conn = None
        try:
            conn = self.pg_pool.getconn()
            cur = conn.cursor()
            cur.execute(query, params)
            rows = cur.fetchall()
            return rows
        except Exception as e:
            print(f"[WordStats] Error executing fetch: {e}")
            return []
        finally:
            if conn:
                self.pg_pool.putconn(conn)

    def _normalize_guild_id(self, guild_id: Optional[int]) -> int:
        return int(guild_id) if guild_id else 0
