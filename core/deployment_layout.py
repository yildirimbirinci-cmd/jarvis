from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

DEPLOYMENT_SCHEMA_VERSION = 1
NODE_NAMES = {"ALFA", "BETA", "OMEGA"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_local_root() -> Path:
    explicit = os.environ.get("ECHO_DATA_ROOT", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        return Path(local) / "ArtmachAssistant"
    return Path.home() / ".local" / "share" / "ArtmachAssistant"


@dataclass(frozen=True)
class DeploymentPaths:
    application_root: Path
    data_root: Path
    config_root: Path
    logs_root: Path
    cache_root: Path
    temp_root: Path
    engineering_root: Path
    backups_root: Path
    local_memory_root: Path
    shared_memory_cache_root: Path
    node_state_root: Path

    @classmethod
    def resolve(cls, application_root: str | Path, data_root: str | Path | None = None) -> "DeploymentPaths":
        app = Path(application_root).expanduser().resolve()
        data = Path(data_root).expanduser().resolve() if data_root is not None else _default_local_root().resolve()
        return cls(
            application_root=app,
            data_root=data,
            config_root=data / "config",
            logs_root=data / "logs",
            cache_root=data / "cache",
            temp_root=data / "temp",
            engineering_root=data / "engineering",
            backups_root=data / "backups",
            local_memory_root=data / "memory" / "local",
            shared_memory_cache_root=data / "memory" / "shared_cache",
            node_state_root=data / "nodes",
        )

    def ensure_persistent_tree(self) -> None:
        for path in (
            self.data_root, self.config_root, self.logs_root, self.cache_root,
            self.temp_root, self.engineering_root, self.backups_root,
            self.local_memory_root, self.shared_memory_cache_root, self.node_state_root,
        ):
            path.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class NodeIdentity:
    node_name: str
    machine_id: str
    project_workspace: str
    shared_memory_root: str = ""
    schema_version: int = DEPLOYMENT_SCHEMA_VERSION

    def validate(self) -> None:
        if self.node_name not in NODE_NAMES:
            raise ValueError("node_name must be ALFA, BETA or OMEGA")
        if not re.fullmatch(r"[A-Za-z0-9._-]{3,128}", self.machine_id):
            raise ValueError("machine_id is invalid")
        if not self.project_workspace.strip():
            raise ValueError("project_workspace cannot be empty")


def save_node_identity(identity: NodeIdentity, paths: DeploymentPaths) -> Path:
    identity.validate(); paths.ensure_persistent_tree()
    target = paths.node_state_root / "identity.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(identity), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    return target


def load_node_identity(paths: DeploymentPaths) -> NodeIdentity | None:
    path = paths.node_state_root / "identity.json"
    if not path.is_file(): return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    identity = NodeIdentity(**raw); identity.validate(); return identity


def _safe_member(name: str) -> PurePosixPath:
    pure = PurePosixPath(name.replace("\\", "/"))
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise ValueError(f"unsafe migration path: {name}")
    return pure


def export_persistent_data(paths: DeploymentPaths, target_zip: str | Path) -> Path:
    paths.ensure_persistent_tree()
    target = Path(target_zip).expanduser().resolve(); target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    manifest = {"schema_version": 1, "created_at": utc_now(), "kind": "echo-persistent-data"}
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("MIGRATION.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            for path in sorted(paths.data_root.rglob("*")):
                if path.is_symlink() or not path.is_file(): continue
                rel = path.relative_to(paths.data_root).as_posix()
                if rel.startswith("temp/") or rel.startswith("cache/"): continue
                z.write(path, f"data/{rel}")
        os.replace(tmp, target)
    finally:
        if tmp.exists(): tmp.unlink()
    return target


def import_persistent_data(paths: DeploymentPaths, source_zip: str | Path) -> None:
    source = Path(source_zip).expanduser().resolve()
    staging = Path(tempfile.mkdtemp(prefix="echo-migrate-", dir=paths.data_root.parent))
    backup = paths.data_root.with_name(paths.data_root.name + ".migration-backup")
    try:
        with zipfile.ZipFile(source) as z:
            names = z.namelist()
            if "MIGRATION.json" not in names: raise ValueError("migration manifest missing")
            for name in names: _safe_member(name)
            z.extractall(staging)
        manifest = json.loads((staging/"MIGRATION.json").read_text(encoding="utf-8"))
        if manifest.get("kind") != "echo-persistent-data": raise ValueError("invalid migration bundle")
        incoming = staging / "data"
        if not incoming.is_dir(): raise ValueError("migration data missing")
        shutil.rmtree(backup, ignore_errors=True)
        if paths.data_root.exists(): os.replace(paths.data_root, backup)
        try:
            shutil.copytree(incoming, paths.data_root)
        except Exception:
            shutil.rmtree(paths.data_root, ignore_errors=True)
            if backup.exists(): os.replace(backup, paths.data_root)
            raise
        shutil.rmtree(backup, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
