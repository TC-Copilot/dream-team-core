#!/usr/bin/env python3
"""Focused checks for local cache-first state deltas and source checkpoints."""
from __future__ import annotations

import gc
import pathlib
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

import app as appmod  # noqa: E402


def main() -> int:
    original_db_path = appmod.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            appmod.DB_PATH = pathlib.Path(tmp) / "state-delta.db"
            appmod.init_db()
            db = appmod.connect()
            try:
                db.execute(
                    "INSERT INTO how_sync_checkpoints(source, checkpoint, updated_at) "
                    "VALUES(?,?,?)",
                    ("document", "cursor-7", "2026-08-18T14:00:00Z"),
                )
                db.commit()
            finally:
                db.close()

            full = appmod.get_state()
            assert full["sync"] == {
                "mode": "full",
                "requestedSince": "",
                "acceptedSince": "",
                "highWaterMark": full["serverTime"],
                "sourceCheckpoints": {"how": {"document": "cursor-7"}},
            }
            assert appmod.to_agent_view(full)["sync"] == full["sync"]

            cursor = full["sync"]["highWaterMark"]
            delta = appmod.get_state(cursor)
            assert delta["sync"]["mode"] == "delta"
            assert delta["sync"]["requestedSince"] == cursor
            assert delta["sync"]["acceptedSince"] == cursor
            assert delta["sync"]["highWaterMark"] == delta["serverTime"]
            assert delta["sync"]["sourceCheckpoints"] == {
                "how": {"document": "cursor-7"}
            }
            assert "approvals" in delta
            assert "jobs" in delta
            assert "watches" in delta

            invalid = appmod.get_state("not-a-timestamp")
            assert invalid["sync"]["mode"] == "full"
            assert invalid["sync"]["requestedSince"] == "not-a-timestamp"
            assert invalid["sync"]["acceptedSince"] == ""

            prompt = (REPO_ROOT / "app" / "prompts" / "attention-major.md").read_text(
                encoding="utf-8"
            )
            for required in (
                "local SQLite-backed response as the run's cache",
                "Cache-first never means evidence-free",
                "approval gates",
                "Casey knowledge capture",
                "meeting",
                "email",
                "Quinn review",
            ):
                assert required in prompt, required

            print("[ok] state delta cache contract")
            return 0
        finally:
            appmod.DB_PATH = original_db_path
            gc.collect()


if __name__ == "__main__":
    raise SystemExit(main())
