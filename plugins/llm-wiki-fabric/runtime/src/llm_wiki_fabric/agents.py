"""Bounded, persistent announcements; federation membership is not caller authentication."""

import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from .catalog import FabricError

FEDERATION_URL = "https://la3d-llm-agents.github.io/index.json"
RETENTION = 7 * 86400
RECENT = 15 * 60
REFRESH = 300
MAX_SESSIONS = 1000


def fetch_federation():
    # Only this operator-selected source is fetched, never an announced card URL.
    with httpx.Client(timeout=5, follow_redirects=False) as client:
        with client.stream("GET", FEDERATION_URL) as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > 2_000_000:
                    raise ValueError("Federation index exceeds size limit")
    data = json.loads(body)
    if not isinstance(data, dict) or not isinstance(data.get("agents"), list):
        raise ValueError("Invalid federation index")
    if len(data["agents"]) > 5000:
        raise ValueError("Too many federation entries")
    entries = {}
    for item in data["agents"]:
        agent_id = item["id"]
        validate_identity(agent_id, item["card_url"])
        if agent_id in entries:
            raise ValueError("Duplicate federation identity")
        metadata = {
            key: item.get(key)
            for key in (
                "id",
                "owner_repo",
                "description",
                "card_url",
                "home_url",
                "topics",
                "capabilities",
            )
        }
        if not isinstance(metadata["description"], str) or len(metadata["description"]) > 4000:
            raise ValueError("Invalid description")
        for key in ("topics", "capabilities"):
            values = metadata[key]
            if (
                not isinstance(values, list)
                or len(values) > 100
                or any(not isinstance(v, str) or len(v) > 1000 for v in values)
            ):
                raise ValueError("Invalid agent metadata")
        entries[agent_id] = metadata
    return {"generated_at": data.get("generated_at"), "entries": entries}


def validate_identity(agent_id, card_url):
    if not isinstance(agent_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", agent_id
    ):
        raise FabricError(
            "invalid_agent", "Use the exact owner/repository identity from the wiki card."
        )
    if not isinstance(card_url, str) or len(card_url) > 1000:
        raise FabricError("invalid_card", "A published GitHub wiki card URL is required.")
    parsed = urlsplit(card_url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"/[^/]+/[^/]+/wiki/Card_[^/]+", parsed.path)
    ):
        raise FabricError(
            "invalid_card", "Use an HTTPS github.com wiki Card URL without query or fragment."
        )


class AgentRegistry:
    def __init__(self, path=":memory:", *, fetcher=fetch_federation, clock=time.time):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.RLock()
        self.fetcher, self.clock = fetcher, clock
        self.last_attempt = None
        self.refresh_error = False
        with self.db:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS federation (id INTEGER PRIMARY KEY, fetched REAL, payload TEXT)"
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, public_id TEXT, agent_id TEXT, payload TEXT, first_seen REAL, last_seen REAL, discoveries TEXT)"
            )

    def _prune(self, now):
        self.db.execute("DELETE FROM sessions WHERE last_seen < ?", (now - RETENTION,))

    def _federation(self, refresh=False):
        now = self.clock()
        row = self.db.execute("SELECT fetched, payload FROM federation WHERE id=1").fetchone()
        if (
            refresh
            and (not row or now - row[0] >= REFRESH)
            and (self.last_attempt is None or now - self.last_attempt >= REFRESH)
        ):
            self.last_attempt = now
            try:
                data = self.fetcher()
                self.db.execute(
                    "INSERT OR REPLACE INTO federation VALUES (1, ?, ?)", (now, json.dumps(data))
                )
                row = now, json.dumps(data)
                self.refresh_error = False
            except (httpx.HTTPError, ValueError, KeyError, TypeError, FabricError):
                self.refresh_error = True
        age = max(0, now - row[0]) if row else None
        return (json.loads(row[1]) if row else {"entries": {}}), {
            "source": FEDERATION_URL,
            "fetched_at": row[0] if row else None,
            "age_seconds": age,
            "state": "unavailable"
            if not row
            else "stale"
            if self.refresh_error or age >= REFRESH
            else "fresh",
        }

    def announce(self, agent_id, card_url, session_id=None, client=None):
        validate_identity(agent_id, card_url)
        if client is not None and (len(client) > 80 or any(ord(c) < 32 for c in client)):
            raise FabricError(
                "invalid_client", "Client label must be at most 80 printable characters."
            )
        with self.lock, self.db:
            now = self.clock()
            self._prune(now)
            existing = None
            if session_id:
                existing = self.db.execute(
                    "SELECT agent_id, public_id FROM sessions WHERE token=?",
                    (self._hash(session_id),),
                ).fetchone()
                if not existing or existing[0] != agent_id:
                    raise FabricError(
                        "unknown_session",
                        "Session expired or does not match; announce without session_id to start a new session.",
                    )
            data, freshness = self._federation(refresh=True)
            metadata = data["entries"].get(agent_id)
            if metadata and metadata["card_url"] != card_url:
                raise FabricError(
                    "card_mismatch", "Card URL differs from the federation entry for this identity."
                )
            membership = (
                "listed"
                if metadata
                else "unknown"
                if freshness["state"] in {"stale", "unavailable"}
                else "not_listed"
            )
            payload = {
                "id": agent_id,
                "card_url": card_url,
                "metadata": metadata,
                "federation_membership": membership,
                "identity_verification": "unverified",
                "federation": {**freshness, "generated_at": data.get("generated_at")},
                "client": client,
            }
            if existing:
                public_id = existing[1]
                self.db.execute(
                    "UPDATE sessions SET payload=?, last_seen=? WHERE token=?",
                    (json.dumps(payload), now, self._hash(session_id)),
                )
            else:
                if self.db.execute("SELECT count(*) FROM sessions").fetchone()[0] >= MAX_SESSIONS:
                    raise FabricError(
                        "announcement_capacity",
                        "Recent session capacity reached; discovery remains available.",
                    )
                session_id, public_id = secrets.token_urlsafe(32), secrets.token_hex(12)
                self.db.execute(
                    "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        self._hash(session_id),
                        public_id,
                        agent_id,
                        json.dumps(payload),
                        now,
                        now,
                        "{}",
                    ),
                )
            return {
                "ok": True,
                **payload,
                "session_id": session_id,
                "public_session_id": public_id,
                "last_seen": now,
                "retention_seconds": RETENTION,
            }

    @staticmethod
    def _hash(token):
        if not isinstance(token, str) or len(token) > 200:
            raise FabricError("unknown_session", "Invalid announcement session ID.")
        return hashlib.sha256(token.encode()).hexdigest()

    def observe(self, session_id, resource_ids=()):
        if not session_id:
            return None
        with self.lock, self.db:
            now = self.clock()
            self._prune(now)
            token = self._hash(session_id)
            row = self.db.execute(
                "SELECT discoveries FROM sessions WHERE token=?", (token,)
            ).fetchone()
            if not row:
                return {
                    "state": "unknown_session",
                    "message": "Announce again without session_id; discovery succeeded.",
                }
            discoveries = json.loads(row[0])
            for resource_id in resource_ids:
                discoveries[resource_id] = now
            self.db.execute(
                "UPDATE sessions SET last_seen=?, discoveries=? WHERE token=?",
                (now, json.dumps(discoveries), token),
            )
            return {"state": "recorded", "last_seen": now}

    def snapshot(self):
        with self.lock, self.db:
            now = self.clock()
            self._prune(now)
            data, freshness = self._federation()
            agents = {}
            for public_id, agent_id, payload, first, last, discoveries in self.db.execute(
                "SELECT public_id, agent_id, payload, first_seen, last_seen, discoveries FROM sessions ORDER BY last_seen DESC, public_id"
            ):
                info = json.loads(payload)
                fetched = info["federation"].get("fetched_at")
                if fetched is not None:
                    info["federation"]["age_seconds"] = max(0, now - fetched)
                    if now - fetched >= REFRESH:
                        info["federation"]["state"] = "stale"
                if agent_id not in agents:
                    agents[agent_id] = {
                        **info,
                        "first_seen": first,
                        "last_seen": last,
                        "sessions": [],
                        "discoveries": {},
                    }
                agent = agents[agent_id]
                agent["first_seen"] = min(agent["first_seen"], first)
                agent["sessions"].append(
                    {
                        "id": public_id,
                        "client": info["client"],
                        "first_seen": first,
                        "last_seen": last,
                        "recent": now - last < RECENT,
                    }
                )
                for resource, seen in json.loads(discoveries).items():
                    agent["discoveries"][resource] = max(
                        seen, agent["discoveries"].get(resource, 0)
                    )
            rows = list(agents.values())
            revision = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
            return {
                "ok": True,
                "agents": rows,
                "activity_revision": revision,
                "as_of": now,
                "retention_seconds": RETENTION,
                "recent_window_seconds": RECENT,
                "federation": {**freshness, "generated_at": data.get("generated_at")},
            }

    def graph_view(self, graph):
        activity = self.snapshot()
        resources = {
            n["data"]["resource_id"]: n["data"]["id"]
            for n in graph["nodes"]
            if n["data"]["type"] == "resource"
        }
        for agent in activity["agents"]:
            node_id = "urn:fabric:agent:" + quote(agent["id"], safe="")
            graph["nodes"].append(
                {
                    "data": {
                        "id": node_id,
                        "type": "agent",
                        "agent_id": agent["id"],
                        "label": agent["id"],
                        "last_seen": agent["last_seen"],
                    }
                }
            )
            for resource, seen in agent["discoveries"].items():
                if resource in resources:
                    graph["edges"].append(
                        {
                            "data": {
                                "id": node_id + ":discovered:" + quote(resource, safe=""),
                                "source": node_id,
                                "target": resources[resource],
                                "type": "discovered",
                                "label": "discovered",
                                "last_seen": seen,
                            }
                        }
                    )
        return {**graph, "activity": activity}
