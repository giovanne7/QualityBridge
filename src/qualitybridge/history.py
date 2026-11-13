import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from qualitybridge.models import RunResult


class HistoryStore:
    def __init__(self, path: Path):
        self.path = path

    def save(self, result: RunResult) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=10) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, project TEXT NOT NULL, started_at TEXT NOT NULL,
                payload TEXT NOT NULL)""")
            self._create_quarantine_tables(connection)
            connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?)",
                (
                    result.id,
                    result.project,
                    result.started_at.isoformat(),
                    result.model_dump_json(),
                ),
            )

    def recent(self, project: str, limit: int = 10) -> list[RunResult]:
        if not self.path.exists():
            return []
        with sqlite3.connect(self.path, timeout=10) as connection:
            rows = connection.execute(
                "SELECT payload FROM runs WHERE project = ? ORDER BY started_at DESC LIMIT ?",
                (project, limit),
            ).fetchall()
        return [RunResult.model_validate_json(row[0]) for row in rows]

    @staticmethod
    def _create_quarantine_tables(connection: sqlite3.Connection) -> None:
        connection.execute("""CREATE TABLE IF NOT EXISTS quarantines (
            project TEXT NOT NULL, runner TEXT NOT NULL, suite TEXT NOT NULL,
            classname TEXT NOT NULL, test_name TEXT NOT NULL, status TEXT NOT NULL,
            reason TEXT NOT NULL, actor TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY (project, runner, suite, classname, test_name))""")
        connection.execute("""CREATE TABLE IF NOT EXISTS quarantine_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL,
            runner TEXT NOT NULL, suite TEXT NOT NULL, classname TEXT NOT NULL,
            test_name TEXT NOT NULL, action TEXT NOT NULL, reason TEXT NOT NULL,
            actor TEXT NOT NULL, created_at TEXT NOT NULL)""")

    def quarantine(
        self,
        project: str,
        runner: str,
        suite: str,
        classname: str,
        test_name: str,
        action: Literal["quarantine", "release"],
        reason: str,
        actor: str,
    ) -> None:
        if action not in ("quarantine", "release"):
            raise ValueError("Ação deve ser quarantine ou release.")
        if not reason.strip() or not actor.strip():
            raise ValueError("Informe motivo e responsável pela alteração.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC).isoformat()
        status = "quarantined" if action == "quarantine" else "active"
        with sqlite3.connect(self.path, timeout=10) as connection:
            self._create_quarantine_tables(connection)
            connection.execute(
                """INSERT INTO quarantines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project, runner, suite, classname, test_name) DO UPDATE SET
                status=excluded.status, reason=excluded.reason, actor=excluded.actor,
                updated_at=excluded.updated_at""",
                (
                    project,
                    runner,
                    suite,
                    classname,
                    test_name,
                    status,
                    reason.strip(),
                    actor.strip(),
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO quarantine_events
                (project, runner, suite, classname, test_name, action, reason, actor, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project,
                    runner,
                    suite,
                    classname,
                    test_name,
                    action,
                    reason.strip(),
                    actor.strip(),
                    now,
                ),
            )

    def quarantined(self, project: str) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        with sqlite3.connect(self.path, timeout=10) as connection:
            self._create_quarantine_tables(connection)
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM quarantines WHERE project=? AND status='quarantined' "
                "ORDER BY runner, suite, classname, test_name",
                (project,),
            ).fetchall()
        return [dict(row) for row in rows]

    def quarantine_reasons(
        self, project: str, runner: str, suite: str
    ) -> dict[tuple[str, str], str]:
        if not self.path.exists():
            return {}
        with sqlite3.connect(self.path, timeout=10) as connection:
            existing = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='quarantines'"
            ).fetchone()
            if not existing:
                return {}
            rows = connection.execute(
                """SELECT classname, test_name, reason FROM quarantines
                WHERE project=? AND runner=? AND suite=? AND status='quarantined'""",
                (project, runner, suite),
            ).fetchall()
        return {(classname, name): reason for classname, name, reason in rows}

    def quarantine_audit(self, project: str, limit: int = 100) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        with sqlite3.connect(self.path, timeout=10) as connection:
            self._create_quarantine_tables(connection)
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM quarantine_events WHERE project=? ORDER BY id DESC LIMIT ?",
                (project, limit),
            ).fetchall()
        return [dict(row) for row in rows]
