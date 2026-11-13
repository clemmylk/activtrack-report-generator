"""Crash-safe progress: reuse only explicit resume, identical inputs and fresh source evidence."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def fingerprint(legs, config):
    body = json.dumps({"legs": legs, "config": config}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()


def load_checkpoint(path, expected, now=None, max_hours=6):
    path = Path(path)
    if not path.exists():
        raise ValueError("No saved progress yet. Use Run tracking now first.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1 or data.get("fingerprint") != expected:
        raise ValueError("The source report or site instructions changed. Start a fresh run so old results are not mixed in.")
    now = now or datetime.now(timezone.utc)
    fresh = {}
    for k, record in data.get("completed", {}).items():
        try:
            age = (now - datetime.fromisoformat(record["checked_at"])).total_seconds()
        except (ValueError, KeyError, TypeError):
            continue
        if 0 <= age <= max_hours * 3600:
            fresh[k] = record
    data["completed"] = fresh
    return data


def save_checkpoint(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def query_key(site, vessel):
    return json.dumps([site, vessel], ensure_ascii=False)
