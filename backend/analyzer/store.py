"""SQLite 缓存层。

多线程方案选的是「主线程统一读写」：worker 线程只发网络请求，
本类的所有方法只在主线程被调用，因此一条 sqlite3 连接用到底即可，
不需要 threading.local，也不会碰 database is locked。
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path


class SummaryStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS summary_cache (
                cache_key    TEXT PRIMARY KEY,
                model        TEXT NOT NULL,
                prompt_ver   TEXT NOT NULL,
                path         TEXT NOT NULL,
                role         TEXT,
                summary      TEXT,
                key_entities TEXT,
                created_at   TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def get(self, cache_key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT model, prompt_ver, path, role, summary, key_entities "
            "FROM summary_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row is None:
            return None
        return {
            "model": row[0],
            "prompt_ver": row[1],
            "path": row[2],
            "role": row[3],
            "summary": row[4],
            # key_entities 存的是 JSON 字符串，取出来还原成列表
            "key_entities": json.loads(row[5]) if row[5] else [],
        }

    def put(self, cache_key: str, record: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO summary_cache "
            "(cache_key, model, prompt_ver, path, role, summary, key_entities, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cache_key,
                record["model"],
                record["prompt_ver"],
                record["path"],
                record["role"],
                record["summary"],
                json.dumps(record["key_entities"], ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        # 每条单独 commit：进程随时可能被打断，宁可慢一点也不丢已经花掉的 token
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
