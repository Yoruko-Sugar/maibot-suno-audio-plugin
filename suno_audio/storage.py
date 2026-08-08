"""Suno 音频任务的 SQLite 持久化。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple
import json
import sqlite3
import uuid

from .models import AudioTrack, GenerationRequest, VENDOR_IN_PROGRESS_STATUSES, VendorTaskSnapshot


ACTIVE_STATUSES = {"queued", "submitting", "submitted", "pending", "delivering"}
RESUMABLE_STATUSES = {"submitted", "pending", "delivering"}


def utc_now() -> str:
    """返回可按字符串排序的 UTC 时间。"""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AudioRepository:
    """线程安全的插件独立 SQLite Repository。"""

    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = database_path
        self._connection = sqlite3.connect(database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = RLock()
        self._initialize()

    def close(self) -> None:
        """关闭数据库连接。"""

        with self._lock:
            self._connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS audio_jobs (
                    id TEXT PRIMARY KEY,
                    short_id TEXT NOT NULL UNIQUE,
                    vendor_task_id TEXT UNIQUE,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    vendor_status TEXT NOT NULL DEFAULT '',
                    progress INTEGER NOT NULL DEFAULT 0,
                    platform TEXT NOT NULL,
                    stream_id TEXT NOT NULL,
                    group_id TEXT NOT NULL DEFAULT '',
                    requester_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    original_prompt TEXT NOT NULL,
                    optimized_prompt_json TEXT NOT NULL,
                    optimizer_model TEXT NOT NULL DEFAULT '',
                    triggering_message_id TEXT NOT NULL DEFAULT '',
                    error_type TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    delivery_status TEXT NOT NULL DEFAULT 'pending',
                    lyrics_text TEXT NOT NULL DEFAULT '',
                    raw_result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    submitted_at TEXT,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_audio_jobs_stream_created
                    ON audio_jobs(stream_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_audio_jobs_user_created
                    ON audio_jobs(requester_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_audio_jobs_status_updated
                    ON audio_jobs(status, updated_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_audio_jobs_trigger_unique
                    ON audio_jobs(stream_id, triggering_message_id)
                    WHERE triggering_message_id <> '';

                CREATE TABLE IF NOT EXISTS audio_tracks (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    audio_index INTEGER NOT NULL,
                    audio_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    duration_seconds REAL,
                    lyrics TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '',
                    audio_url TEXT NOT NULL DEFAULT '',
                    image_url TEXT NOT NULL DEFAULT '',
                    image_large_url TEXT NOT NULL DEFAULT '',
                    video_url TEXT NOT NULL DEFAULT '',
                    mime_type TEXT NOT NULL DEFAULT '',
                    file_size INTEGER,
                    local_path TEXT NOT NULL DEFAULT '',
                    sha256 TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES audio_jobs(id) ON DELETE CASCADE,
                    UNIQUE(job_id, audio_index)
                );

                CREATE TABLE IF NOT EXISTS audio_delivery_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    audio_index INTEGER,
                    delivery_mode TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES audio_jobs(id) ON DELETE CASCADE
                );
                """
            )

    def create_job(
        self,
        *,
        request: GenerationRequest,
        platform: str,
        stream_id: str,
        group_id: str,
        requester_id: str,
        triggering_message_id: str = "",
    ) -> Tuple[Dict[str, Any], bool]:
        """创建本地任务；相同触发消息已存在时返回原任务。"""

        now = utc_now()
        trigger_id = triggering_message_id.strip()
        with self._lock, self._connection:
            if trigger_id:
                existing = self._connection.execute(
                    "SELECT * FROM audio_jobs WHERE stream_id = ? AND triggering_message_id = ?",
                    (stream_id, trigger_id),
                ).fetchone()
                if existing is not None:
                    return dict(existing), False
            duplicate = self._connection.execute(
                """
                SELECT * FROM audio_jobs
                WHERE stream_id = ? AND requester_id = ? AND request_hash = ?
                  AND status IN ('queued', 'submitting', 'submitted', 'pending', 'delivering')
                ORDER BY created_at DESC LIMIT 1
                """,
                (stream_id, requester_id, request.request_hash),
            ).fetchone()
            if duplicate is not None:
                return dict(duplicate), False

            job_id = uuid.uuid4().hex
            short_id = self._new_short_id()
            request_json = request.to_json()
            self._connection.execute(
                """
                INSERT INTO audio_jobs (
                    id, short_id, operation, status, platform, stream_id, group_id,
                    requester_id, request_hash, request_json, original_prompt,
                    optimized_prompt_json, optimizer_model, triggering_message_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'submitting', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    short_id,
                    request.operation.value,
                    platform,
                    stream_id,
                    group_id,
                    requester_id,
                    request.request_hash,
                    request_json,
                    request.original_prompt,
                    request_json,
                    request.optimizer_model,
                    trigger_id,
                    now,
                    now,
                ),
            )
            row = self._connection.execute("SELECT * FROM audio_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise RuntimeError("创建音频任务后无法读回记录")
            return dict(row), True

    def _new_short_id(self) -> str:
        for _ in range(10):
            short_id = f"snd_{uuid.uuid4().hex[:8].upper()}"
            exists = self._connection.execute("SELECT 1 FROM audio_jobs WHERE short_id = ?", (short_id,)).fetchone()
            if exists is None:
                return short_id
        raise RuntimeError("无法生成唯一音频任务短 ID")

    def mark_submitted(self, job_id: str, vendor_task_id: str) -> None:
        now = utc_now()
        self._execute_update(
            """
            UPDATE audio_jobs
            SET vendor_task_id = ?, status = 'submitted', vendor_status = 'submitted',
                submitted_at = ?, updated_at = ?, error_type = '', error_message = ''
            WHERE id = ?
            """,
            (vendor_task_id, now, now, job_id),
        )

    def update_snapshot(self, job_id: str, snapshot: VendorTaskSnapshot) -> None:
        local_status = "pending" if snapshot.status in VENDOR_IN_PROGRESS_STATUSES else snapshot.status
        now = utc_now()
        self._execute_update(
            """
            UPDATE audio_jobs
            SET status = ?, vendor_status = ?, progress = ?, error_message = ?, updated_at = ?
            WHERE id = ?
            """,
            (local_status, snapshot.status, snapshot.progress, snapshot.error_message, now, job_id),
        )

    def complete_job(self, job_id: str, snapshot: VendorTaskSnapshot) -> None:
        """原子保存完成状态和全部音轨。"""

        now = utc_now()
        raw_result_json = json.dumps(snapshot.raw_result, ensure_ascii=False, sort_keys=True)
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE audio_jobs
                SET status = 'completed', vendor_status = 'completed', progress = 100,
                    lyrics_text = ?, raw_result_json = ?, completed_at = ?, updated_at = ?,
                    error_type = '', error_message = ''
                WHERE id = ?
                """,
                (snapshot.lyrics_text, raw_result_json, now, now, job_id),
            )
            self._connection.execute("DELETE FROM audio_tracks WHERE job_id = ?", (job_id,))
            for track in snapshot.tracks:
                self._insert_track(job_id, track, now)

    def _insert_track(self, job_id: str, track: AudioTrack, now: str) -> None:
        self._connection.execute(
            """
            INSERT INTO audio_tracks (
                id, job_id, audio_index, audio_id, title, duration_seconds, lyrics,
                tags, audio_url, image_url, image_large_url, video_url, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                job_id,
                track.audio_index,
                track.audio_id,
                track.title,
                track.duration_seconds,
                track.lyrics,
                track.tags,
                track.audio_url,
                track.image_url,
                track.image_large_url,
                track.video_url,
                now,
            ),
        )

    def mark_failed(self, job_id: str, *, status: str, error_type: str, error_message: str) -> None:
        self._execute_update(
            """
            UPDATE audio_jobs
            SET status = ?, error_type = ?, error_message = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, error_type, error_message, utc_now(), job_id),
        )

    def set_delivery_status(self, job_id: str, status: str) -> None:
        self._execute_update(
            "UPDATE audio_jobs SET delivery_status = ?, updated_at = ? WHERE id = ?",
            (status, utc_now(), job_id),
        )

    def record_delivery_attempt(
        self,
        *,
        job_id: str,
        audio_index: Optional[int],
        delivery_mode: str,
        success: bool,
        error_message: str = "",
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO audio_delivery_attempts (
                    job_id, audio_index, delivery_mode, success, error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (job_id, audio_index, delivery_mode, int(success), error_message, utc_now()),
            )

    def get_job(self, identifier: str) -> Optional[Dict[str, Any]]:
        normalized = identifier.strip()
        if not normalized:
            return None
        with self._lock:
            exact = self._connection.execute(
                """
                SELECT * FROM audio_jobs
                WHERE id = ? OR short_id = ? OR vendor_task_id = ?
                LIMIT 1
                """,
                (normalized, normalized, normalized),
            ).fetchone()
            if exact is not None:
                return dict(exact)
            prefix_rows = self._connection.execute(
                """
                SELECT * FROM audio_jobs
                WHERE id LIKE ? OR short_id LIKE ? OR vendor_task_id LIKE ?
                ORDER BY created_at DESC LIMIT 2
                """,
                (f"{normalized}%", f"{normalized}%", f"{normalized}%"),
            ).fetchall()
            if len(prefix_rows) > 1:
                raise ValueError("任务 ID 前缀不唯一，请输入更多字符")
            return dict(prefix_rows[0]) if prefix_rows else None

    def get_job_by_trigger(self, stream_id: str, triggering_message_id: str) -> Optional[Dict[str, Any]]:
        """按来源消息读取已创建任务，用于阻止 Tool 重复触发计费。"""

        trigger_id = triggering_message_id.strip()
        if not trigger_id:
            return None
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM audio_jobs WHERE stream_id = ? AND triggering_message_id = ? LIMIT 1",
                (stream_id, trigger_id),
            ).fetchone()
            return dict(row) if row is not None else None

    def get_tracks(self, job_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM audio_tracks WHERE job_id = ? ORDER BY audio_index",
                (job_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_stream_jobs(self, stream_id: str, *, limit: int = 10, offset: int = 0) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM audio_jobs
                WHERE stream_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?
                """,
                (stream_id, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def latest_user_job(self, stream_id: str, requester_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM audio_jobs
                WHERE stream_id = ? AND requester_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (stream_id, requester_id),
            ).fetchone()
            return dict(row) if row is not None else None

    def list_resumable_jobs(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM audio_jobs
                WHERE status IN ('submitted', 'pending', 'delivering')
                   OR (status = 'completed' AND delivery_status IN ('pending', 'delivering'))
                   OR (
                       status = 'tracking_error'
                       AND error_type = 'protocol_error'
                       AND error_message = '未知供应商任务状态：processing'
                   )
                ORDER BY created_at
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def count_active_stream_jobs(self, stream_id: str) -> int:
        return self._count(
            """
            SELECT COUNT(*) FROM audio_jobs
            WHERE stream_id = ? AND status IN ('queued', 'submitting', 'submitted', 'pending', 'delivering')
            """,
            (stream_id,),
        )

    def count_user_jobs_since(self, requester_id: str, since: str) -> int:
        return self._count(
            "SELECT COUNT(*) FROM audio_jobs WHERE requester_id = ? AND created_at >= ?",
            (requester_id, since),
        )

    def latest_user_created_at(self, requester_id: str) -> str:
        with self._lock:
            row = self._connection.execute(
                "SELECT created_at FROM audio_jobs WHERE requester_id = ? ORDER BY created_at DESC LIMIT 1",
                (requester_id,),
            ).fetchone()
            return str(row["created_at"]) if row is not None else ""

    def _count(self, sql: str, parameters: tuple[Any, ...]) -> int:
        with self._lock:
            row = self._connection.execute(sql, parameters).fetchone()
            return int(row[0]) if row is not None else 0

    def _execute_update(self, sql: str, parameters: tuple[Any, ...]) -> None:
        with self._lock, self._connection:
            cursor = self._connection.execute(sql, parameters)
            if cursor.rowcount != 1:
                raise KeyError("音频任务不存在或更新数量异常")
