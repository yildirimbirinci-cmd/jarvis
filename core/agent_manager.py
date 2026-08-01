"""Constitution-controlled agent registration and run authorization manager."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Iterable, Iterator

from artmach_assistant.config import DATA_DIR
from artmach_assistant.core.constitution import AgentPolicy, ModuleConstitutionContext

_MAX_REGISTRY_ROW_BYTES = 1024 * 1024


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> object:
    raise ValueError(f"Non-finite JSON number is not allowed: {value}")


def _load_strict_json_object(raw: bytes) -> dict[str, object]:
    text = raw.decode("utf-8")
    payload = json.loads(
        text,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_non_finite,
    )
    if not isinstance(payload, dict):
        raise ValueError("Registry record must be a JSON object.")
    return payload


def _iter_bounded_jsonl_rows(path: Path) -> Iterator[bytes]:
    with path.open("rb") as handle:
        while True:
            row = handle.readline(_MAX_REGISTRY_ROW_BYTES + 1)
            if not row:
                return
            if len(row) > _MAX_REGISTRY_ROW_BYTES:
                if not row.endswith(b"\n"):
                    while True:
                        remainder = handle.readline(_MAX_REGISTRY_ROW_BYTES + 1)
                        if not remainder or remainder.endswith(b"\n"):
                            break
                continue
            stripped = row.strip()
            if stripped:
                yield stripped


@dataclass(frozen=True)
class AgentDefinition:
    agent_id: str
    name: str
    purpose: str
    capabilities: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    max_risk: str = "low"


@dataclass
class AgentRun:
    run_id: str
    agent_id: str
    task: str
    requested_tools: list[str]
    risk: str
    depth: int
    state: str
    created_at: str
    approved_at: str = ""


class AgentManager:
    def __init__(self, constitution: ModuleConstitutionContext) -> None:
        self.policy = AgentPolicy(constitution)
        self.registry_path = DATA_DIR / "agent_registry.jsonl"
        self.audit_path = DATA_DIR / "agent_runtime_audit.jsonl"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._agents: dict[str, AgentDefinition] = {}
        self._load_registry()

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"

    @staticmethod
    def _required_text(value: object, *, field: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field} must be text.")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{field} cannot be empty.")
        return cleaned

    @classmethod
    def _text_items(cls, values: Iterable[str], *, field: str) -> tuple[str, ...]:
        if isinstance(values, (str, bytes)) or values is None:
            raise TypeError(f"{field} must be an iterable of text values.")
        try:
            iterator = iter(values)
        except TypeError as exc:
            raise TypeError(f"{field} must be an iterable of text values.") from exc
        result: list[str] = []
        seen: set[str] = set()
        for value in iterator:
            cleaned = cls._required_text(value, field=f"{field} item")
            if cleaned in seen:
                continue
            seen.add(cleaned)
            result.append(cleaned)
        return tuple(result)

    def register(
        self,
        *,
        name: str,
        purpose: str,
        capabilities: Iterable[str],
        allowed_tools: Iterable[str],
        max_risk: str = "low",
    ) -> AgentDefinition:
        self.policy.require("agent.register")
        clean_name = self._required_text(name, field="Agent name")
        clean_purpose = self._required_text(purpose, field="Agent purpose")
        caps = self._text_items(capabilities, field="Capabilities")
        tools = self._text_items(allowed_tools, field="Allowed tools")
        if not caps:
            raise ValueError("At least one agent capability is required.")
        if not isinstance(max_risk, str) or max_risk not in self.policy.rules["risk_levels"]:
            raise ValueError(f"Invalid agent risk level: {max_risk!r}")
        agent = AgentDefinition(self._id("AGENT"), clean_name, clean_purpose, caps, tools, max_risk)
        with self._lock:
            self._agents[agent.agent_id] = agent
            try:
                self._append(self.registry_path, asdict(agent))
            except Exception:
                self._agents.pop(agent.agent_id, None)
                raise
        return agent

    def authorize_run(
        self,
        *,
        agent_id: str,
        task: str,
        requested_tools: Iterable[str] = (),
        risk: str = "low",
        depth: int = 0,
        approved: bool = False,
    ) -> AgentRun:
        self.policy.require("agent.authorize_run", approved=approved)
        self.policy.validate_depth(depth)
        clean_agent_id = self._required_text(agent_id, field="Agent ID")
        clean_task = self._required_text(task, field="Agent task")
        agent = self._require_agent(clean_agent_id)
        tools = list(self._text_items(requested_tools, field="Requested tools"))
        denied = sorted(set(tools).difference(agent.allowed_tools))
        if denied:
            raise PermissionError("Agent requested tools outside its allow-list: " + ", ".join(denied))
        levels = list(self.policy.rules["risk_levels"])
        if not isinstance(risk, str) or risk not in levels or levels.index(risk) > levels.index(agent.max_risk):
            raise PermissionError("Requested risk level exceeds the agent authorization boundary.")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        run = AgentRun(self._id("RUN"), clean_agent_id, clean_task, tools, risk, depth, "authorized", now, now)
        with self._lock:
            self._append(self.audit_path, asdict(run))
        return run

    def validate_tool(self, agent_id: str, tool_name: str) -> bool:
        self.policy.require("agent.use_tool")
        clean_agent_id = self._required_text(agent_id, field="Agent ID")
        clean_tool_name = self._required_text(tool_name, field="Tool name")
        agent = self._require_agent(clean_agent_id)
        if clean_tool_name not in agent.allowed_tools:
            raise PermissionError(f"{clean_tool_name} is not allowed for agent {agent.name}.")
        return True

    def list_agents(self) -> list[AgentDefinition]:
        self.policy.require("agent.read")
        with self._lock:
            return list(self._agents.values())

    def _require_agent(self, agent_id: str) -> AgentDefinition:
        with self._lock:
            try:
                return self._agents[agent_id]
            except KeyError as exc:
                raise KeyError(f"Registered agent not found: {agent_id}") from exc

    def _load_registry(self) -> None:
        if not self.registry_path.exists():
            return
        allowed_risks = set(self.policy.rules["risk_levels"])
        loaded: dict[str, AgentDefinition] = {}
        try:
            rows = _iter_bounded_jsonl_rows(self.registry_path)
            for line in rows:
                try:
                    raw = _load_strict_json_object(line)
                    raw["capabilities"] = self._text_items(raw.get("capabilities", ()), field="Capabilities")
                    raw["allowed_tools"] = self._text_items(raw.get("allowed_tools", ()), field="Allowed tools")
                    agent = AgentDefinition(**raw)
                    agent_id = self._required_text(agent.agent_id, field="Agent ID")
                    name = self._required_text(agent.name, field="Agent name")
                    purpose = self._required_text(agent.purpose, field="Agent purpose")
                    max_risk = self._required_text(agent.max_risk, field="Agent risk level")
                    if not agent.capabilities or max_risk not in allowed_risks:
                        continue
                    if agent_id in loaded:
                        continue
                    loaded[agent_id] = AgentDefinition(
                        agent_id=agent_id,
                        name=name,
                        purpose=purpose,
                        capabilities=agent.capabilities,
                        allowed_tools=agent.allowed_tools,
                        max_risk=max_risk,
                    )
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                    continue
        except OSError:
            return
        with self._lock:
            self._agents.update(loaded)

    @staticmethod
    def _append(path: Path, payload: dict) -> None:
        """Append one durable JSONL record without leaving a partial row."""
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if len(encoded) > _MAX_REGISTRY_ROW_BYTES:
            raise ValueError("Registry or audit record exceeds the maximum row size.")

        if len(encoded) > _MAX_REGISTRY_ROW_BYTES:
            raise ValueError("Registry or audit record exceeds the maximum allowed row size.")
        with path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            original_size = handle.tell()
            try:
                written = handle.write(encoded)
                if written != len(encoded):
                    raise OSError(
                        f"Short JSONL write: expected {len(encoded)} bytes, wrote {written}."
                    )
                handle.flush()
                os.fsync(handle.fileno())
            except Exception:
                try:
                    handle.seek(original_size)
                    handle.truncate()
                    handle.flush()
                    os.fsync(handle.fileno())
                except OSError:
                    pass
                raise
