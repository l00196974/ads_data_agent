# Artifact 系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个 per-user 共享的 artifact 工作区系统，让 SKILL.md 包能输出大型分析产物（多文件 / 多类型）并通过文件系统持久化、跨 channel 差异化呈现。

**Architecture:** Skill 通过 env 注入的 `ADS_AGENT_ARTIFACT_DIR` 自由写入文件，写完后向 stderr 输出约定行通知 runner；runner 推 SSE `artifact_updated` 事件；前端按需调 REST API 拉 manifest 与文件渲染。Artifact 内容**不进 LLM context**（避免 token 爆炸）。

**Tech Stack:** Python 3.13 + FastAPI + pydantic + pyyaml + Vue 3 + ECharts + marked

**Spec:** `docs/superpowers/specs/2026-04-30-artifact-system-design.md`

---

## Task 1: Artifact 模型基础（创建 + manifest 读写）

**Files:**
- Create: `agent/artifact.py`
- Create: `tests/unit/agent/test_artifact.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/agent/test_artifact.py
"""Tests for the Artifact model."""
from pathlib import Path

import yaml

from agent.artifact import Artifact


def test_create_artifact_creates_dir_and_writes_manifest(tmp_path: Path):
    art = Artifact.create(
        user_id="alice",
        title="KA 客户 CTR 异常分析",
        slug="ka-ctr-anomaly",
        artifacts_root=tmp_path,
        related_skills=["query-metrics", "detect-anomaly"],
    )
    assert art.dir.exists()
    assert art.dir.is_dir()
    assert art.dir.parent == tmp_path

    manifest_path = art.dir / "manifest.yaml"
    assert manifest_path.exists()
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert manifest["user_id"] == "alice"
    assert manifest["title"] == "KA 客户 CTR 异常分析"
    assert manifest["artifact_id"] == art.artifact_id
    assert manifest["related_skills"] == ["query-metrics", "detect-anomaly"]
    assert "-ka-ctr-anomaly" in art.artifact_id


def test_artifact_id_is_sortable_by_time(tmp_path: Path):
    """同一 slug 但不同时间生成的 id 字典序与时间序一致（前缀是时间戳）。"""
    import time

    art1 = Artifact.create(user_id="u", title="t", slug="x", artifacts_root=tmp_path)
    time.sleep(1.1)
    art2 = Artifact.create(user_id="u", title="t", slug="x", artifacts_root=tmp_path)
    assert art1.artifact_id < art2.artifact_id
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd D:/workplace/ads_data_agent && .venv/Scripts/python.exe -m pytest tests/unit/agent/test_artifact.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'agent.artifact'`

- [ ] **Step 3: 实现最小 agent/artifact.py**

```python
# agent/artifact.py
"""Artifact 模型：用户工作区里的一份分析产物（一个目录 + manifest）。

设计前提见 docs/superpowers/specs/2026-04-30-artifact-system-design.md。

核心约定：
- artifact_id 格式：{YYYY-MM-DD-HHmmss}-{slug}（时间戳保证唯一 + 可排序）
- 目录结构自由，仅 manifest.yaml 是契约文件
- immutable：写完不变，没有"更新 artifact"的 API（同 id 后写覆盖前写）
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml


_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass
class Artifact:
    """一份 artifact 的内存视图，绑定到磁盘上的目录。"""

    artifact_id: str
    user_id: str
    title: str
    dir: Path

    @classmethod
    def create(
        cls,
        user_id: str,
        title: str,
        slug: str,
        artifacts_root: Path,
        artifact_type: str = "report",
        description: str = "",
        entry_files: Optional[list[str]] = None,
        related_skills: Optional[list[str]] = None,
        related_conversation_id: Optional[str] = None,
    ) -> Artifact:
        """创建一个新 artifact 目录 + 写入初始 manifest.yaml。

        slug 由调用方传入，要求小写字母数字 + 连字符。
        """
        if not _SLUG_PATTERN.match(slug):
            raise ValueError(f"非法 slug: {slug!r}（要求 ^[a-z0-9][a-z0-9-]*$）")

        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d-%H%M%S")
        artifact_id = f"{timestamp}-{slug}"

        artifacts_root.mkdir(parents=True, exist_ok=True)
        artifact_dir = artifacts_root / artifact_id
        artifact_dir.mkdir(parents=True, exist_ok=True)

        manifest = {
            "artifact_id": artifact_id,
            "title": title,
            "created_at": now.isoformat(timespec="seconds"),
            "user_id": user_id,
            "artifact_type": artifact_type,
            "description": description,
            "entry_files": entry_files or [],
            "related_skills": related_skills or [],
        }
        if related_conversation_id:
            manifest["related_conversation_id"] = related_conversation_id

        (artifact_dir / "manifest.yaml").write_text(
            yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return cls(
            artifact_id=artifact_id,
            user_id=user_id,
            title=title,
            dir=artifact_dir,
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd D:/workplace/ads_data_agent && .venv/Scripts/python.exe -m pytest tests/unit/agent/test_artifact.py -v`

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
cd D:/workplace/ads_data_agent
git add agent/artifact.py tests/unit/agent/test_artifact.py
git commit -m "feat(artifact): basic Artifact model with create + manifest write"
```

---

## Task 2: Artifact 加载（read manifest + 列表 + 目录树）

**Files:**
- Modify: `agent/artifact.py`
- Modify: `tests/unit/agent/test_artifact.py`

- [ ] **Step 1: 加测试**

追加到 `tests/unit/agent/test_artifact.py` 末尾：

```python
def test_load_artifact_from_disk(tmp_path: Path):
    art = Artifact.create(user_id="alice", title="t", slug="x", artifacts_root=tmp_path)
    loaded = Artifact.load(tmp_path, art.artifact_id)
    assert loaded.artifact_id == art.artifact_id
    assert loaded.user_id == "alice"
    assert loaded.title == "t"


def test_load_missing_artifact_raises(tmp_path: Path):
    import pytest

    with pytest.raises(FileNotFoundError):
        Artifact.load(tmp_path, "2026-04-30-000000-nonexistent")


def test_list_artifacts_sorted_by_created_at_desc(tmp_path: Path):
    import time

    a1 = Artifact.create(user_id="u", title="first", slug="a", artifacts_root=tmp_path)
    time.sleep(1.1)
    a2 = Artifact.create(user_id="u", title="second", slug="b", artifacts_root=tmp_path)
    time.sleep(1.1)
    a3 = Artifact.create(user_id="u", title="third", slug="c", artifacts_root=tmp_path)

    summaries = Artifact.list_summaries(tmp_path)
    ids = [s["artifact_id"] for s in summaries]
    assert ids == [a3.artifact_id, a2.artifact_id, a1.artifact_id]


def test_list_artifacts_skips_dirs_without_manifest(tmp_path: Path):
    Artifact.create(user_id="u", title="t", slug="a", artifacts_root=tmp_path)
    (tmp_path / "2026-01-01-000000-broken").mkdir()
    summaries = Artifact.list_summaries(tmp_path)
    assert len(summaries) == 1


def test_tree_lists_files_and_dirs(tmp_path: Path):
    art = Artifact.create(user_id="u", title="t", slug="x", artifacts_root=tmp_path)
    (art.dir / "INDEX.md").write_text("# index", encoding="utf-8")
    (art.dir / "data").mkdir()
    (art.dir / "data" / "raw.json").write_text("{}", encoding="utf-8")

    tree = art.tree()
    paths = {node["path"] for node in _flatten(tree)}
    assert "manifest.yaml" in paths
    assert "INDEX.md" in paths
    assert "data" in paths
    assert "data/raw.json" in paths


def _flatten(tree: list) -> list:
    out = []
    for node in tree:
        out.append(node)
        if node["type"] == "dir":
            out.extend(_flatten(node.get("children", [])))
    return out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/agent/test_artifact.py -v`

Expected: FAIL with `AttributeError: type object 'Artifact' has no attribute 'load'`

- [ ] **Step 3: 加 load / list_summaries / tree 实现**

在 `agent/artifact.py` 的 `Artifact` 类内（`create` 方法之后）追加：

```python
    @classmethod
    def load(cls, artifacts_root: Path, artifact_id: str) -> Artifact:
        """从磁盘加载已存在的 artifact。"""
        artifact_dir = artifacts_root / artifact_id
        manifest_path = artifact_dir / "manifest.yaml"
        if not manifest_path.exists():
            raise FileNotFoundError(f"artifact 不存在或未完成（缺 manifest.yaml）：{artifact_id}")
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        return cls(
            artifact_id=manifest["artifact_id"],
            user_id=manifest["user_id"],
            title=manifest.get("title", ""),
            dir=artifact_dir,
        )

    @staticmethod
    def list_summaries(artifacts_root: Path) -> list[dict]:
        """列出 artifacts_root 下所有有效 artifact 的 manifest 摘要，按 created_at 倒序。"""
        if not artifacts_root.is_dir():
            return []
        summaries: list[dict] = []
        for entry in artifacts_root.iterdir():
            if not entry.is_dir():
                continue
            manifest_path = entry / "manifest.yaml"
            if not manifest_path.exists():
                continue
            try:
                manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            except (yaml.YAMLError, OSError):
                continue
            file_count = sum(1 for p in entry.rglob("*") if p.is_file())
            summaries.append({
                "artifact_id": manifest.get("artifact_id", entry.name),
                "title": manifest.get("title", ""),
                "created_at": manifest.get("created_at", ""),
                "artifact_type": manifest.get("artifact_type", "mixed"),
                "description": manifest.get("description", ""),
                "file_count": file_count,
            })
        summaries.sort(key=lambda s: s["created_at"], reverse=True)
        return summaries

    def manifest(self) -> dict:
        """读取并返回 manifest dict。"""
        return yaml.safe_load((self.dir / "manifest.yaml").read_text(encoding="utf-8"))

    def tree(self) -> list[dict]:
        """返回目录树（递归）：[{path, type, size?, children?}, ...]"""
        return _build_tree(self.dir, prefix="")
```

并在文件末尾（类外）追加：

```python
def _build_tree(root: Path, prefix: str) -> list[dict]:
    nodes: list[dict] = []
    for entry in sorted(root.iterdir()):
        rel_path = f"{prefix}{entry.name}" if not prefix else f"{prefix}/{entry.name}"
        if entry.is_dir():
            nodes.append({
                "path": rel_path,
                "type": "dir",
                "children": _build_tree(entry, rel_path),
            })
        else:
            nodes.append({
                "path": rel_path,
                "type": "file",
                "size": entry.stat().st_size,
            })
    return nodes
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/agent/test_artifact.py -v`

Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add agent/artifact.py tests/unit/agent/test_artifact.py
git commit -m "feat(artifact): load + list_summaries + tree"
```

---

## Task 3: Artifact 删除 + 路径校验

**Files:**
- Modify: `agent/artifact.py`
- Modify: `tests/unit/agent/test_artifact.py`

- [ ] **Step 1: 加测试**

追加到 `tests/unit/agent/test_artifact.py`：

```python
def test_delete_artifact_removes_dir(tmp_path: Path):
    art = Artifact.create(user_id="u", title="t", slug="x", artifacts_root=tmp_path)
    assert art.dir.exists()
    Artifact.delete(tmp_path, art.artifact_id)
    assert not art.dir.exists()


def test_delete_missing_artifact_raises(tmp_path: Path):
    import pytest

    with pytest.raises(FileNotFoundError):
        Artifact.delete(tmp_path, "2026-04-30-000000-nope")


def test_safe_file_path_resolves_within_artifact(tmp_path: Path):
    art = Artifact.create(user_id="u", title="t", slug="x", artifacts_root=tmp_path)
    (art.dir / "data").mkdir()
    (art.dir / "data" / "x.json").write_text("{}", encoding="utf-8")
    p = art.safe_file_path("data/x.json")
    assert p == (art.dir / "data" / "x.json").resolve()


def test_safe_file_path_rejects_traversal(tmp_path: Path):
    import pytest

    art = Artifact.create(user_id="u", title="t", slug="x", artifacts_root=tmp_path)
    with pytest.raises(ValueError, match="非法路径"):
        art.safe_file_path("../../../etc/passwd")
    with pytest.raises(ValueError, match="非法路径"):
        art.safe_file_path("/etc/passwd")


def test_safe_file_path_rejects_missing_file(tmp_path: Path):
    import pytest

    art = Artifact.create(user_id="u", title="t", slug="x", artifacts_root=tmp_path)
    with pytest.raises(FileNotFoundError):
        art.safe_file_path("nonexistent.json")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/agent/test_artifact.py -v`

Expected: FAIL with `AttributeError: type object 'Artifact' has no attribute 'delete'`

- [ ] **Step 3: 实现 delete + safe_file_path**

在 `agent/artifact.py` 顶部 import 区追加：

```python
import shutil
```

在 `Artifact` 类内（`tree` 方法之后）追加：

```python
    @staticmethod
    def delete(artifacts_root: Path, artifact_id: str) -> None:
        """删除整个 artifact 目录。"""
        artifact_dir = artifacts_root / artifact_id
        if not artifact_dir.is_dir():
            raise FileNotFoundError(f"artifact 不存在：{artifact_id}")
        shutil.rmtree(artifact_dir)

    def safe_file_path(self, relative_path: str) -> Path:
        """把 relative_path 解析到 artifact 目录内的绝对路径，禁止穿越。"""
        if relative_path.startswith("/") or ".." in Path(relative_path).parts:
            raise ValueError(f"非法路径：{relative_path}")

        target = (self.dir / relative_path).resolve()
        artifact_root_resolved = self.dir.resolve()
        if not target.is_relative_to(artifact_root_resolved):
            raise ValueError(f"非法路径（跳出 artifact 目录）：{relative_path}")
        if not target.is_file():
            raise FileNotFoundError(f"文件不存在：{relative_path}")
        return target
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/agent/test_artifact.py -v`

Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add agent/artifact.py tests/unit/agent/test_artifact.py
git commit -m "feat(artifact): delete + safe_file_path with traversal guard"
```

---

## Task 4: UserSpace 加 artifacts_dir

**Files:**
- Modify: `agent/user_space.py`
- Modify: `tests/unit/agent/test_user_space.py`

- [ ] **Step 1: 加测试**

追加到 `tests/unit/agent/test_user_space.py` 末尾：

```python
def test_user_space_has_artifacts_dir(tmp_path):
    from agent.user_space import UserSpace

    us = UserSpace("alice", data_dir=str(tmp_path))
    assert us.artifacts_dir.exists()
    assert us.artifacts_dir.is_dir()
    assert us.artifacts_dir.name == "artifacts"
    assert us.artifacts_dir.parent.name == "alice"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/agent/test_user_space.py -v`

Expected: FAIL with `AttributeError: 'UserSpace' object has no attribute 'artifacts_dir'`

- [ ] **Step 3: 改 agent/user_space.py 加 artifacts_dir**

在 `__init__` 方法已有的属性赋值后追加：

```python
        self.artifacts_dir = self.data_dir / "artifacts"
```

修改 `_ensure_dirs` 方法的目录列表：

```python
    def _ensure_dirs(self):
        for d in [self.data_dir, self.skills_dir, self.memory_dir, self.artifacts_dir]:
            d.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/agent/test_user_space.py -v`

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add agent/user_space.py tests/unit/agent/test_user_space.py
git commit -m "feat(user-space): add artifacts_dir for per-user artifact workspace"
```

---

## Task 5: REST API endpoints

**Files:**
- Create: `api/artifacts.py`
- Create: `tests/unit/api/test_artifacts.py`
- Modify: `main.py`

- [ ] **Step 1: 写完整测试**

```python
# tests/unit/api/test_artifacts.py
"""Artifact REST endpoints tests."""
import io
from pathlib import Path
import zipfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """启 FastAPI app 但 patch data_dir 到 tmp_path。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    import importlib
    import agent.config as cfg_mod
    cfg = cfg_mod.load_config("config.yaml")
    cfg.persistence.data_dir = str(tmp_path)
    monkeypatch.setattr(cfg_mod, "load_config", lambda *a, **kw: cfg)

    import api.artifacts as artifacts_mod
    importlib.reload(artifacts_mod)

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(artifacts_mod.router, prefix="/api/artifacts")
    return TestClient(app)


def _make_artifact(tmp_path: Path, user_id: str, slug: str) -> str:
    from agent.artifact import Artifact
    from agent.user_space import UserSpace
    us = UserSpace(user_id, data_dir=str(tmp_path))
    art = Artifact.create(
        user_id=user_id,
        title=f"test {slug}",
        slug=slug,
        artifacts_root=us.artifacts_dir,
        description="test desc",
    )
    (art.dir / "INDEX.md").write_text("# hello", encoding="utf-8")
    (art.dir / "data").mkdir()
    (art.dir / "data" / "raw.json").write_text('{"k":1}', encoding="utf-8")
    return art.artifact_id


def test_list_returns_user_artifacts(client, tmp_path):
    aid1 = _make_artifact(tmp_path, "alice", "first")
    aid2 = _make_artifact(tmp_path, "alice", "second")
    _make_artifact(tmp_path, "bob", "other")

    resp = client.get("/api/artifacts/alice")
    assert resp.status_code == 200
    data = resp.json()
    ids = [a["artifact_id"] for a in data["artifacts"]]
    assert aid1 in ids and aid2 in ids
    assert all("other" not in i for i in ids)


def test_list_empty_for_unknown_user(client):
    resp = client.get("/api/artifacts/nobody")
    assert resp.status_code == 200
    assert resp.json() == {"artifacts": []}


def test_get_manifest_with_tree(client, tmp_path):
    aid = _make_artifact(tmp_path, "alice", "test")
    resp = client.get(f"/api/artifacts/alice/{aid}/manifest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["manifest"]["artifact_id"] == aid
    paths = {n["path"] for n in _flat(body["tree"])}
    assert "manifest.yaml" in paths
    assert "INDEX.md" in paths
    assert "data" in paths
    assert "data/raw.json" in paths


def test_get_manifest_404_for_unknown(client):
    resp = client.get("/api/artifacts/alice/2026-01-01-000000-nope/manifest")
    assert resp.status_code == 404


def test_get_file_returns_content(client, tmp_path):
    aid = _make_artifact(tmp_path, "alice", "test")
    resp = client.get(f"/api/artifacts/alice/{aid}/file/INDEX.md")
    assert resp.status_code == 200
    assert resp.text == "# hello"


def test_get_file_rejects_traversal(client, tmp_path):
    aid = _make_artifact(tmp_path, "alice", "test")
    resp = client.get(f"/api/artifacts/alice/{aid}/file/../../../etc/passwd")
    assert resp.status_code == 400


def test_get_file_404_missing(client, tmp_path):
    aid = _make_artifact(tmp_path, "alice", "test")
    resp = client.get(f"/api/artifacts/alice/{aid}/file/nope.json")
    assert resp.status_code == 404


def test_zip_endpoint_returns_zip_with_all_files(client, tmp_path):
    aid = _make_artifact(tmp_path, "alice", "test")
    resp = client.get(f"/api/artifacts/alice/{aid}/zip")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = set(zf.namelist())
    assert "manifest.yaml" in names
    assert "INDEX.md" in names
    assert "data/raw.json" in names


def test_delete_removes_artifact(client, tmp_path):
    aid = _make_artifact(tmp_path, "alice", "test")
    resp = client.delete(f"/api/artifacts/alice/{aid}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted", "artifact_id": aid}
    listing = client.get("/api/artifacts/alice").json()
    assert all(a["artifact_id"] != aid for a in listing["artifacts"])


def test_delete_404_for_unknown(client):
    resp = client.delete("/api/artifacts/alice/2026-01-01-000000-nope")
    assert resp.status_code == 404


def test_artifact_id_format_validation(client):
    resp = client.get("/api/artifacts/alice/invalid-id/manifest")
    assert resp.status_code == 400


def _flat(tree):
    out = []
    for n in tree:
        out.append(n)
        if n.get("type") == "dir":
            out.extend(_flat(n.get("children", [])))
    return out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/api/test_artifacts.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'api.artifacts'`

- [ ] **Step 3: 实现 api/artifacts.py**

```python
# api/artifacts.py
"""Artifact REST endpoints."""
import io
import re
import zipfile

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from agent.artifact import Artifact
from agent.config import load_config
from agent.user_space import UserSpace


router = APIRouter()
cfg = load_config()


_ARTIFACT_ID_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}-[a-z0-9][a-z0-9-]*$")


def _validate_artifact_id(artifact_id: str) -> None:
    if not _ARTIFACT_ID_PATTERN.match(artifact_id):
        raise HTTPException(400, f"非法 artifact_id 格式：{artifact_id}")


def _user_space(user_id: str) -> UserSpace:
    if "/" in user_id or ".." in user_id:
        raise HTTPException(400, "非法 user_id")
    return UserSpace(user_id, cfg.persistence.data_dir)


@router.get("/{user_id}")
async def list_artifacts(user_id: str):
    us = _user_space(user_id)
    return {"artifacts": Artifact.list_summaries(us.artifacts_dir)}


@router.get("/{user_id}/{artifact_id}/manifest")
async def get_manifest(user_id: str, artifact_id: str):
    _validate_artifact_id(artifact_id)
    us = _user_space(user_id)
    try:
        art = Artifact.load(us.artifacts_dir, artifact_id)
    except FileNotFoundError:
        raise HTTPException(404, f"artifact 不存在：{artifact_id}")
    return {"manifest": art.manifest(), "tree": art.tree()}


@router.get("/{user_id}/{artifact_id}/file/{relative_path:path}")
async def get_file(user_id: str, artifact_id: str, relative_path: str):
    _validate_artifact_id(artifact_id)
    us = _user_space(user_id)
    try:
        art = Artifact.load(us.artifacts_dir, artifact_id)
    except FileNotFoundError:
        raise HTTPException(404, f"artifact 不存在：{artifact_id}")
    try:
        file_path = art.safe_file_path(relative_path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError:
        raise HTTPException(404, f"文件不存在：{relative_path}")
    return FileResponse(file_path)


@router.get("/{user_id}/{artifact_id}/zip")
async def download_zip(user_id: str, artifact_id: str):
    _validate_artifact_id(artifact_id)
    us = _user_space(user_id)
    try:
        art = Artifact.load(us.artifacts_dir, artifact_id)
    except FileNotFoundError:
        raise HTTPException(404, f"artifact 不存在：{artifact_id}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in art.dir.rglob("*"):
            if path.is_file():
                arcname = path.relative_to(art.dir).as_posix()
                zf.write(path, arcname=arcname)
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{artifact_id}.zip"'},
    )


@router.delete("/{user_id}/{artifact_id}")
async def delete_artifact(user_id: str, artifact_id: str):
    _validate_artifact_id(artifact_id)
    us = _user_space(user_id)
    try:
        Artifact.delete(us.artifacts_dir, artifact_id)
    except FileNotFoundError:
        raise HTTPException(404, f"artifact 不存在：{artifact_id}")
    return {"status": "deleted", "artifact_id": artifact_id}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/api/test_artifacts.py -v`

Expected: 11 passed

- [ ] **Step 5: 挂 router 到 main.py**

修改 `main.py` 的 import：

```python
from api import artifacts, chat, skills
```

并在 `app.include_router(skills.router, ...)` 后追加：

```python
app.include_router(artifacts.router, prefix="/api/artifacts", tags=["artifacts"])
```

- [ ] **Step 6: 验证后端能起来 + endpoint 注册**

Run: `.venv/Scripts/python.exe -c "from main import app; print([r.path for r in app.routes if 'artifact' in r.path])"`

Expected: 输出含 `/api/artifacts/{user_id}` 等几条路径

- [ ] **Step 7: Commit**

```bash
git add api/artifacts.py tests/unit/api/test_artifacts.py main.py
git commit -m "feat(api): artifact REST endpoints (list/manifest/file/zip/delete)"
```

---

## Task 6: BaseChannel 加 send_artifact_updated

**Files:**
- Modify: `api/channel/base.py`
- Modify: `api/channel/web_sse.py`
- Modify: `api/channel/cli.py`

- [ ] **Step 1: 给 BaseChannel 加 abstract 方法**

修改 `api/channel/base.py`，在 `wait_for_confirm` 之前加：

```python
    @abstractmethod
    async def send_artifact_updated(self, artifact_id: str, action: str) -> None:
        """通知用户产生 / 更新了一个 artifact。

        action: "created" | "updated"
        Channel 自行决定怎么呈现：WebSSE 推 SSE event；CLI 打印简短通知。
        """
```

- [ ] **Step 2: WebSSEChannel 实现**

修改 `api/channel/web_sse.py`，在 `send_plan` 后追加：

```python
    async def send_artifact_updated(self, artifact_id: str, action: str) -> None:
        data = json.dumps({"artifact_id": artifact_id, "action": action}, ensure_ascii=False)
        await self._queue.put(f"event: artifact_updated\ndata: {data}\n\n")
```

- [ ] **Step 3: CLIChannel 实现**

修改 `api/channel/cli.py`，在 `send_plan` 后追加：

```python
    async def send_artifact_updated(self, artifact_id: str, action: str) -> None:
        emoji = "📦" if action == "created" else "🔄"
        print(f"\n{emoji} Artifact {action}: {artifact_id}")
```

- [ ] **Step 4: 验证 channel 还能 import**

Run: `.venv/Scripts/python.exe -c "from api.channel import WebSSEChannel; from api.channel.cli import CLIChannel; import asyncio; q = asyncio.Queue(); ch = WebSSEChannel('u','s', q); print('ok')"`

Expected: `ok`

- [ ] **Step 5: 跑现有测试**

Run: `.venv/Scripts/python.exe -m pytest tests/ -v`

Expected: 全过

- [ ] **Step 6: Commit**

```bash
git add api/channel/base.py api/channel/web_sse.py api/channel/cli.py
git commit -m "feat(channel): add send_artifact_updated abstract + WebSSE/CLI impls"
```

---

## Task 7: skill_loader 解析 stderr artifact 通知

**Files:**
- Modify: `agent/skill_loader.py`
- Create: `tests/unit/agent/test_skill_loader.py`

- [ ] **Step 1: 写测试**

```python
# tests/unit/agent/test_skill_loader.py
"""skill_loader 的 stderr artifact 通知解析。"""
import asyncio
import os
from pathlib import Path

import pytest


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    """造一个测试 skill：打印 ADS_AGENT_* env 并向 stderr 输出 artifact 通知行。"""
    pkg = tmp_path / "echo-env-skill"
    pkg.mkdir()
    (pkg / "SKILL.md").write_text(
        """---
name: echo-env
description: 测试 skill
---
""",
        encoding="utf-8",
    )
    (pkg / "package.json").write_text(
        '{"name":"echo-env","bin":{"echo-env":"bin/echo-env.py"}}',
        encoding="utf-8",
    )
    bin_dir = pkg / "bin"
    bin_dir.mkdir()
    script = bin_dir / "echo-env.py"
    script.write_text(
        """import os, sys
artifact_dir = os.environ.get("ADS_AGENT_ARTIFACT_DIR", "")
artifact_id = os.environ.get("ADS_AGENT_ARTIFACT_ID", "")
print(f"DIR={artifact_dir}")
print(f"ID={artifact_id}")
sys.stderr.write(f"<<<ADS_AGENT:ARTIFACT_UPDATED:artifact_id={artifact_id}>>>\\n")
""",
        encoding="utf-8",
    )
    return tmp_path


def test_run_command_extracts_artifact_id_from_stderr(skills_root):
    from agent.skill_loader import (
        load_md_skills,
        consume_pending_artifact_ids,
    )

    consume_pending_artifact_ids()  # 清残留

    pkg = load_md_skills(skills_root, user_dir=None)
    assert pkg.tools, "expected one run_command tool"
    tool = pkg.tools[0]

    os.environ["ADS_AGENT_ARTIFACT_ID"] = "2026-04-30-120000-test"
    os.environ["ADS_AGENT_ARTIFACT_DIR"] = "/tmp/x"
    try:
        output = asyncio.run(tool.coroutine(command="echo-env"))
    finally:
        del os.environ["ADS_AGENT_ARTIFACT_ID"]
        del os.environ["ADS_AGENT_ARTIFACT_DIR"]

    assert "DIR=/tmp/x" in output
    assert "ID=2026-04-30-120000-test" in output

    ids = consume_pending_artifact_ids()
    assert ids == ["2026-04-30-120000-test"]


def test_extract_artifact_ids_pattern():
    from agent.skill_loader import _extract_artifact_ids

    ids, clean = _extract_artifact_ids(
        "some output\n<<<ADS_AGENT:ARTIFACT_UPDATED:artifact_id=2026-04-30-120000-x>>>\nmore"
    )
    assert ids == ["2026-04-30-120000-x"]
    assert "<<<ADS_AGENT:" not in clean
    assert "some output" in clean


def test_extract_artifact_ids_handles_multiple():
    from agent.skill_loader import _extract_artifact_ids

    text = (
        "<<<ADS_AGENT:ARTIFACT_UPDATED:artifact_id=a>>>\n"
        "<<<ADS_AGENT:ARTIFACT_UPDATED:artifact_id=b>>>\n"
    )
    ids, clean = _extract_artifact_ids(text)
    assert ids == ["a", "b"]
    assert clean.strip() == ""


def test_extract_artifact_ids_no_match():
    from agent.skill_loader import _extract_artifact_ids

    ids, clean = _extract_artifact_ids("just plain text")
    assert ids == []
    assert clean == "just plain text"


def test_consume_drains_pending(skills_root):
    """consume 一次后 contextvar 应该清空。"""
    from agent.skill_loader import consume_pending_artifact_ids, _pending_artifact_ids

    _pending_artifact_ids.set(["x", "y"])
    assert consume_pending_artifact_ids() == ["x", "y"]
    assert consume_pending_artifact_ids() == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/agent/test_skill_loader.py -v`

Expected: FAIL with `ImportError`

- [ ] **Step 3: 改 agent/skill_loader.py**

在文件顶部 import 区追加：

```python
import re
from contextvars import ContextVar
```

在文件末尾追加：

```python
_ARTIFACT_NOTIFICATION_PATTERN = re.compile(
    r"<<<ADS_AGENT:ARTIFACT_UPDATED:artifact_id=([^>]+)>>>"
)


def _extract_artifact_ids(text: str) -> tuple[list[str], str]:
    """从文本里抽所有 artifact_id 通知行，返 (ids, cleaned_text)。"""
    ids = _ARTIFACT_NOTIFICATION_PATTERN.findall(text)
    cleaned = _ARTIFACT_NOTIFICATION_PATTERN.sub("", text)
    return ids, cleaned


_pending_artifact_ids: ContextVar[list[str]] = ContextVar(
    "_pending_artifact_ids", default=[]
)


def consume_pending_artifact_ids() -> list[str]:
    """Runner 在每次 LLM 工具调用结束后调一次，获取本次产生的 artifact id 并清空。"""
    ids = list(_pending_artifact_ids.get())
    _pending_artifact_ids.set([])
    return ids
```

修改 `_make_run_tool` 内的 `run_command` 协程，找到这两行：

```python
        if proc.returncode != 0:
            return f"[exit {proc.returncode}] {subcmd}\nstderr:\n{err}\nstdout:\n{out}"
        return out
```

替换为：

```python
        artifact_ids, err_clean = _extract_artifact_ids(err)
        if artifact_ids:
            existing = list(_pending_artifact_ids.get())
            existing.extend(artifact_ids)
            _pending_artifact_ids.set(existing)

        if proc.returncode != 0:
            return f"[exit {proc.returncode}] {subcmd}\nstderr:\n{err_clean}\nstdout:\n{out}"
        return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/agent/test_skill_loader.py -v`

Expected: 5 passed

- [ ] **Step 5: 跑全套测试**

Run: `.venv/Scripts/python.exe -m pytest tests/ -v`

Expected: 全过

- [ ] **Step 6: Commit**

```bash
git add agent/skill_loader.py tests/unit/agent/test_skill_loader.py
git commit -m "feat(skill-loader): parse stderr artifact notifications via contextvar"
```

---

## Task 8: Runner 推送 artifact_updated SSE

**Files:**
- Modify: `api/channel/runner.py`
- Modify: `tests/unit/api/test_runner_streaming.py`

- [ ] **Step 1: 加测试**

追加到 `tests/unit/api/test_runner_streaming.py` 末尾：

```python
def test_runner_pushes_artifact_updated_when_skill_notifies(monkeypatch):
    """工具调用结束后 runner 应消费 pending artifact_ids 并推 SSE 事件。"""
    fake_ids_state = {"ids": ["2026-04-30-120000-a", "2026-04-30-120000-b"]}

    def fake_consume():
        ids = fake_ids_state["ids"]
        fake_ids_state["ids"] = []
        return ids

    monkeypatch.setattr("api.channel.runner.consume_pending_artifact_ids", fake_consume)

    emitted = _run_with_events([
        {"event": "on_tool_start", "name": "run_command", "data": {"input": {}}},
        {"event": "on_tool_end", "name": "run_command", "data": {}},
    ])
    artifact_events = [e for e in emitted if e["event"] == "artifact_updated"]
    assert len(artifact_events) == 2
    ids = {e["data"]["artifact_id"] for e in artifact_events}
    assert ids == {"2026-04-30-120000-a", "2026-04-30-120000-b"}
    assert all(e["data"]["action"] == "created" for e in artifact_events)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/api/test_runner_streaming.py -v`

Expected: 新测试 FAIL（artifact_updated 事件没推）

- [ ] **Step 3: 改 runner.py**

修改 `api/channel/runner.py` 顶部 import：

```python
from agent.skill_loader import consume_pending_artifact_ids
```

找到 `on_tool_end` 处理段：

```python
                elif kind == "on_tool_end":
                    tool_name = event.get("name", "tool")
                    if _is_meta_tool(tool_name):
                        continue
                    input_data = event.get("data", {}).get("input", {})
                    await channel.send_step(_format_tool_label(tool_name, input_data), "tool_end")
```

替换为：

```python
                elif kind == "on_tool_end":
                    tool_name = event.get("name", "tool")
                    if _is_meta_tool(tool_name):
                        continue
                    input_data = event.get("data", {}).get("input", {})
                    await channel.send_step(_format_tool_label(tool_name, input_data), "tool_end")
                    for artifact_id in consume_pending_artifact_ids():
                        await channel.send_artifact_updated(artifact_id, "created")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/api/test_runner_streaming.py -v`

Expected: 6 passed

- [ ] **Step 5: 跑全套测试**

Run: `.venv/Scripts/python.exe -m pytest tests/ -v`

Expected: 全过

- [ ] **Step 6: Commit**

```bash
git add api/channel/runner.py tests/unit/api/test_runner_streaming.py
git commit -m "feat(runner): emit artifact_updated SSE events from skill notifications"
```

---

## Task 9: Runner 注入 artifact env per tool call

**Files:**
- Modify: `api/channel/runner.py`
- Modify: `tests/unit/api/test_runner_streaming.py`

- [ ] **Step 1: 加测试**

追加：

```python
def test_runner_injects_artifact_env_per_tool_call(monkeypatch):
    """on_tool_start 时 runner 应在 ADS_AGENT_* env 里设置当前调用的上下文。"""
    import os
    captured = {}

    async def fake_stream():
        yield {"event": "on_tool_start", "name": "run_command", "data": {"input": {"command": "echo"}}}
        captured["dir"] = os.environ.get("ADS_AGENT_ARTIFACT_DIR")
        captured["id"] = os.environ.get("ADS_AGENT_ARTIFACT_ID")
        captured["user"] = os.environ.get("ADS_AGENT_USER_ID")
        yield {"event": "on_tool_end", "name": "run_command", "data": {}}

    from unittest.mock import MagicMock
    channel, _ = _make_channel()
    fake_agent = MagicMock()
    fake_agent.astream_events = lambda *a, **kw: fake_stream()

    from api.channel.runner import AgentRunner
    runner = AgentRunner()
    monkeypatch.setattr("api.channel.runner.consume_pending_artifact_ids", lambda: [])
    asyncio.run(runner.run(channel, "msg", lambda extra_tools=None: fake_agent, {
        "configurable": {"thread_id": "alice_conv1"}
    }))

    assert captured.get("dir") is not None
    assert "alice" in captured.get("dir")
    assert captured.get("user") == "alice"
    import re
    assert re.match(r"^\d{4}-\d{2}-\d{2}-\d{6}-report$", captured["id"] or "")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/api/test_runner_streaming.py -v`

Expected: 新测试 FAIL

- [ ] **Step 3: 改 runner.py 注入 env**

修改 `api/channel/runner.py` 顶部 import 区追加：

```python
import os
from datetime import datetime

from agent.config import load_config
from agent.user_space import UserSpace
```

修改 `AgentRunner.run` 方法，在 `extra_tools = make_default_tools(channel)` 下面、`agent = build_agent_fn(...)` 上面，加：

```python
        # 解析 thread_id 拿 user_id（thread_id = "{user_id}_{conversation_id}"）
        thread_id = config.get("configurable", {}).get("thread_id", "")
        user_id = thread_id.split("_", 1)[0] if thread_id else "anon"
        cfg = load_config()
        user_space = UserSpace(user_id, cfg.persistence.data_dir)
```

修改 `on_tool_start` 处理段，把现有：

```python
                elif kind == "on_tool_start":
                    tool_name = event.get("name", "tool")
                    if _is_meta_tool(tool_name):
                        continue
                    business_tools_started += 1
                    input_data = event.get("data", {}).get("input", {})
                    step_msg = _format_tool_label(tool_name, input_data)
                    await channel.send_step(step_msg, "tool_start")
```

替换为：

```python
                elif kind == "on_tool_start":
                    tool_name = event.get("name", "tool")
                    if _is_meta_tool(tool_name):
                        continue
                    business_tools_started += 1
                    # 为本次工具调用预生成 artifact_id + dir，注入 env 让 skill 选择是否使用
                    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
                    artifact_id = f"{timestamp}-report"
                    artifact_dir = user_space.artifacts_dir / artifact_id
                    os.environ["ADS_AGENT_ARTIFACT_DIR"] = str(artifact_dir)
                    os.environ["ADS_AGENT_ARTIFACT_ID"] = artifact_id
                    os.environ["ADS_AGENT_USER_ID"] = user_id
                    input_data = event.get("data", {}).get("input", {})
                    step_msg = _format_tool_label(tool_name, input_data)
                    await channel.send_step(step_msg, "tool_start")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/api/test_runner_streaming.py -v`

Expected: 7 passed

- [ ] **Step 5: 跑全套测试**

Run: `.venv/Scripts/python.exe -m pytest tests/ -v`

Expected: 全过

- [ ] **Step 6: Commit**

```bash
git add api/channel/runner.py tests/unit/api/test_runner_streaming.py
git commit -m "feat(runner): generate artifact_id + inject ADS_AGENT_* env per tool call"
```

---

## Task 10: 前端 ArtifactCard 组件

**Files:**
- Create: `frontend/src/components/ArtifactCard.vue`

- [ ] **Step 1: 写组件**

```vue
<!-- frontend/src/components/ArtifactCard.vue -->
<template>
  <div class="artifact-card" @click="$emit('open', artifactId)">
    <div class="artifact-icon">📦</div>
    <div class="artifact-content">
      <div class="artifact-title">
        {{ manifest?.title || artifactId }}
      </div>
      <div class="artifact-meta">
        <span v-if="manifest">
          {{ formatType(manifest.artifact_type) }} · {{ formatTime(manifest.created_at) }}
        </span>
        <span v-else class="artifact-loading">加载中...</span>
      </div>
      <div v-if="manifest?.description" class="artifact-desc">
        {{ manifest.description }}
      </div>
    </div>
    <div class="artifact-action">
      <span class="action-text">查看 →</span>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'

const props = defineProps({
  artifactId: { type: String, required: true },
  userId: { type: String, required: true },
})
defineEmits(['open'])

const manifest = ref(null)

async function loadManifest() {
  try {
    const resp = await fetch(`/api/artifacts/${props.userId}/${props.artifactId}/manifest`)
    if (!resp.ok) return
    const body = await resp.json()
    manifest.value = body.manifest
  } catch (_) {}
}

onMounted(loadManifest)
watch(() => props.artifactId, loadManifest)

const TYPE_LABEL = {
  report: '📊 综合报告',
  document: '📄 文档',
  spreadsheet: '📋 电子表格',
  presentation: '📽 演示',
  dataset: '🗃 数据集',
  mixed: '📁 混合产物',
}
function formatType(t) {
  return TYPE_LABEL[t] || '📁 ' + (t || 'unknown')
}
function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleString('zh-CN', { hour12: false })
}
</script>

<style scoped>
.artifact-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: rgba(248, 250, 255, 0.95);
  border: 1px solid #d6e4ff;
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.2s ease;
  align-self: flex-start;
  width: 100%;
  max-width: 560px;
}
.artifact-card:hover {
  background: rgba(232, 240, 255, 0.95);
  border-color: #adc6ff;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
}
.artifact-icon {
  font-size: 28px;
  flex-shrink: 0;
}
.artifact-content {
  flex: 1;
  min-width: 0;
}
.artifact-title {
  font-size: 14px;
  font-weight: 600;
  color: #1a1a1a;
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.artifact-meta {
  font-size: 11px;
  color: #8c8c8c;
}
.artifact-loading {
  color: #c7000b;
  font-style: italic;
}
.artifact-desc {
  font-size: 12px;
  color: #595959;
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.artifact-action {
  flex-shrink: 0;
}
.action-text {
  font-size: 12px;
  color: #2f54eb;
  font-weight: 500;
}
</style>
```

- [ ] **Step 2: build 验证**

Run: `cd D:/workplace/ads_data_agent/frontend && npm run build 2>&1 | tail -3`

Expected: `✓ built in ...ms`

- [ ] **Step 3: Commit**

```bash
cd D:/workplace/ads_data_agent
git add frontend/src/components/ArtifactCard.vue
git commit -m "feat(frontend): ArtifactCard component for chat stream"
```

---

## Task 11: 前端 Preview 组件群（5 个轻量预览器）

**Files:**
- Create: `frontend/src/components/preview/MarkdownPreview.vue`
- Create: `frontend/src/components/preview/EChartsPreview.vue`
- Create: `frontend/src/components/preview/CsvPreview.vue`
- Create: `frontend/src/components/preview/JsonPreview.vue`
- Create: `frontend/src/components/preview/ImagePreview.vue`
- Create: `frontend/src/components/preview/DownloadFallback.vue`

每个组件接收 `{ url, name }` 两个 prop。

- [ ] **Step 1: MarkdownPreview.vue**

```vue
<!-- frontend/src/components/preview/MarkdownPreview.vue -->
<template>
  <div class="md-preview markdown-body" v-html="rendered" />
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps({ url: String, name: String })
const rendered = ref('')

async function load() {
  try {
    const resp = await fetch(props.url)
    const text = await resp.text()
    rendered.value = DOMPurify.sanitize(marked.parse(text))
  } catch (e) {
    rendered.value = DOMPurify.sanitize(`<p style="color:#c7000b">加载失败：${e.message}</p>`)
  }
}
onMounted(load)
watch(() => props.url, load)
</script>

<style scoped>
.md-preview {
  padding: 20px;
  font-size: 14px;
  line-height: 1.7;
  color: #1a1a1a;
}
.md-preview :deep(h1) { font-size: 1.6em; margin: 0.6em 0; }
.md-preview :deep(h2) { font-size: 1.3em; margin: 0.6em 0; }
.md-preview :deep(p) { margin: 0.6em 0; }
.md-preview :deep(code) { background: #f1f3f5; padding: 0.1em 0.4em; border-radius: 3px; font-size: 0.9em; }
.md-preview :deep(pre) { background: #f6f8fa; padding: 12px; border-radius: 8px; overflow-x: auto; }
</style>
```

- [ ] **Step 2: EChartsPreview.vue**

```vue
<!-- frontend/src/components/preview/EChartsPreview.vue -->
<template>
  <div class="echarts-preview-wrapper">
    <div ref="chartEl" class="echarts-preview" />
    <div v-if="errorMsg" class="echarts-error">加载失败：{{ errorMsg }}</div>
  </div>
</template>

<script setup>
import { ref, onMounted, watch, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({ url: String, name: String })
const chartEl = ref(null)
const errorMsg = ref('')
let chart = null

async function load() {
  errorMsg.value = ''
  try {
    const resp = await fetch(props.url)
    const config = await resp.json()
    if (!chart && chartEl.value) {
      chart = echarts.init(chartEl.value)
    }
    chart?.setOption(config)
  } catch (e) {
    errorMsg.value = e.message
  }
}

onMounted(load)
watch(() => props.url, load)
onBeforeUnmount(() => chart?.dispose())
</script>

<style scoped>
.echarts-preview-wrapper {
  width: 100%;
  height: 100%;
  position: relative;
  min-height: 400px;
}
.echarts-preview {
  width: 100%;
  height: 100%;
  min-height: 400px;
}
.echarts-error {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #c7000b;
  padding: 20px;
}
</style>
```

- [ ] **Step 3: CsvPreview.vue**

```vue
<!-- frontend/src/components/preview/CsvPreview.vue -->
<template>
  <div class="csv-preview">
    <div v-if="error" class="csv-error">{{ error }}</div>
    <div v-else class="csv-meta">共 {{ rows.length }} 行（最多展示前 1000 行）</div>
    <table v-if="rows.length">
      <thead>
        <tr>
          <th v-for="(h, i) in headers" :key="i">{{ h }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, i) in rows.slice(0, 1000)" :key="i">
          <td v-for="(cell, j) in row" :key="j">{{ cell }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'

const props = defineProps({ url: String, name: String })
const headers = ref([])
const rows = ref([])
const error = ref('')

async function load() {
  error.value = ''
  try {
    const resp = await fetch(props.url)
    const text = await resp.text()
    const lines = text.split('\n').filter(l => l.length)
    if (!lines.length) return
    headers.value = parseRow(lines[0])
    rows.value = lines.slice(1).map(parseRow)
  } catch (e) {
    error.value = '加载失败：' + e.message
  }
}

function parseRow(line) {
  return line.split(',')
}

onMounted(load)
watch(() => props.url, load)
</script>

<style scoped>
.csv-preview {
  padding: 16px;
  overflow: auto;
  height: 100%;
}
.csv-meta {
  font-size: 12px;
  color: #8c8c8c;
  margin-bottom: 12px;
}
.csv-error {
  color: #c7000b;
  padding: 16px;
}
table {
  border-collapse: collapse;
  font-size: 13px;
}
th, td {
  border: 1px solid #e9ecef;
  padding: 6px 10px;
  text-align: left;
  white-space: nowrap;
}
th {
  background: #f1f3f5;
  font-weight: 600;
}
</style>
```

- [ ] **Step 4: JsonPreview.vue**

```vue
<!-- frontend/src/components/preview/JsonPreview.vue -->
<template>
  <pre class="json-preview"><code>{{ rendered }}</code></pre>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'

const props = defineProps({ url: String, name: String })
const rendered = ref('')

async function load() {
  try {
    const resp = await fetch(props.url)
    const text = await resp.text()
    if (text.length > 100 * 1024) {
      rendered.value = '文件过大（>100KB），请下载查看：' + props.url
      return
    }
    try {
      rendered.value = JSON.stringify(JSON.parse(text), null, 2)
    } catch {
      rendered.value = text
    }
  } catch (e) {
    rendered.value = '加载失败：' + e.message
  }
}
onMounted(load)
watch(() => props.url, load)
</script>

<style scoped>
.json-preview {
  margin: 0;
  padding: 20px;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 13px;
  background: #f6f8fa;
  height: 100%;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
```

- [ ] **Step 5: ImagePreview.vue**

```vue
<!-- frontend/src/components/preview/ImagePreview.vue -->
<template>
  <div class="image-preview">
    <img :src="url" :alt="name" />
  </div>
</template>

<script setup>
defineProps({ url: String, name: String })
</script>

<style scoped>
.image-preview {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  height: 100%;
  background: #fafbfc;
}
img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}
</style>
```

- [ ] **Step 6: DownloadFallback.vue**

```vue
<!-- frontend/src/components/preview/DownloadFallback.vue -->
<template>
  <div class="download-fallback">
    <div class="dl-icon">📄</div>
    <div class="dl-name">{{ name }}</div>
    <a :href="url" :download="name" class="dl-button">下载到本地查看</a>
  </div>
</template>

<script setup>
defineProps({ url: String, name: String })
</script>

<style scoped>
.download-fallback {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding: 60px 20px;
  height: 100%;
}
.dl-icon { font-size: 48px; }
.dl-name { font-size: 14px; color: #595959; }
.dl-button {
  padding: 8px 20px;
  background: linear-gradient(135deg, #c7000b, #d81a24);
  color: white;
  border-radius: 8px;
  text-decoration: none;
  font-size: 13px;
}
.dl-button:hover { transform: translateY(-1px); }
</style>
```

- [ ] **Step 7: build 验证 + Commit**

```bash
cd D:/workplace/ads_data_agent/frontend && npm run build 2>&1 | tail -3
```

Expected: `✓ built in ...ms`

```bash
cd D:/workplace/ads_data_agent
git add frontend/src/components/preview/
git commit -m "feat(frontend): preview components for md/echarts/csv/json/image + download fallback"
```

---

## Task 12: 前端 ArtifactBrowser 组件

**Files:**
- Create: `frontend/src/components/ArtifactBrowser.vue`

- [ ] **Step 1: 写组件**

```vue
<!-- frontend/src/components/ArtifactBrowser.vue -->
<template>
  <div class="artifact-browser">
    <header class="browser-header">
      <div class="header-title">
        <span class="header-icon">📦</span>
        <span>{{ manifest?.title || artifactId }}</span>
      </div>
      <div class="header-actions">
        <a :href="`/api/artifacts/${userId}/${artifactId}/zip`" :download="`${artifactId}.zip`" class="btn-zip">
          ⬇ 下载 zip
        </a>
        <button class="btn-close" @click="$emit('close')">×</button>
      </div>
    </header>

    <div class="browser-body">
      <aside class="file-tree">
        <FileTreeNode
          v-for="node in tree"
          :key="node.path"
          :node="node"
          :selected="selected"
          @select="onSelect"
        />
      </aside>

      <main class="file-preview">
        <div v-if="!selected" class="preview-empty">选择左侧文件查看内容</div>
        <component
          v-else
          :is="previewComponent(selected)"
          :url="`/api/artifacts/${userId}/${artifactId}/file/${selected}`"
          :name="selected"
        />
      </main>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, watch, h } from 'vue'
import MarkdownPreview from './preview/MarkdownPreview.vue'
import EChartsPreview from './preview/EChartsPreview.vue'
import CsvPreview from './preview/CsvPreview.vue'
import JsonPreview from './preview/JsonPreview.vue'
import ImagePreview from './preview/ImagePreview.vue'
import DownloadFallback from './preview/DownloadFallback.vue'

const props = defineProps({
  userId: { type: String, required: true },
  artifactId: { type: String, required: true },
})
defineEmits(['close'])

const manifest = ref(null)
const tree = ref([])
const selected = ref(null)

async function load() {
  const resp = await fetch(`/api/artifacts/${props.userId}/${props.artifactId}/manifest`)
  if (!resp.ok) return
  const body = await resp.json()
  manifest.value = body.manifest
  tree.value = body.tree
  const entry = body.manifest?.entry_files?.[0]
  if (entry) selected.value = entry
}
onMounted(load)
watch(() => props.artifactId, load)

function onSelect(path) {
  selected.value = path
}

function previewComponent(path) {
  const lower = path.toLowerCase()
  if (lower.endsWith('.md')) return MarkdownPreview
  if (lower.endsWith('.echarts.json')) return EChartsPreview
  if (lower.endsWith('.csv')) return CsvPreview
  if (lower.endsWith('.json') || lower.endsWith('.yaml') || lower.endsWith('.yml')) return JsonPreview
  if (/\.(png|jpe?g|gif|svg|webp)$/.test(lower)) return ImagePreview
  return DownloadFallback
}

const FileTreeNode = {
  name: 'FileTreeNode',
  props: ['node', 'selected'],
  emits: ['select'],
  setup(p, { emit }) {
    const expanded = ref(true)
    return () => {
      const isDir = p.node.type === 'dir'
      if (isDir) {
        return h('div', { class: 'tree-dir' }, [
          h('div', {
            class: 'tree-dir-name',
            onClick: () => { expanded.value = !expanded.value }
          }, [
            h('span', { class: 'tree-toggle' }, expanded.value ? '▼' : '▶'),
            h('span', { class: 'tree-icon' }, '📁'),
            h('span', p.node.path.split('/').pop())
          ]),
          expanded.value ? h('div', { class: 'tree-children' },
            (p.node.children || []).map(child =>
              h(FileTreeNode, {
                key: child.path,
                node: child,
                selected: p.selected,
                onSelect: (path) => emit('select', path)
              })
            )
          ) : null
        ])
      }
      return h('div', {
        class: ['tree-file', { 'tree-selected': p.selected === p.node.path }],
        onClick: () => emit('select', p.node.path)
      }, [
        h('span', { class: 'tree-icon' }, fileIcon(p.node.path)),
        h('span', { class: 'tree-name' }, p.node.path.split('/').pop())
      ])
    }
  }
}

function fileIcon(path) {
  const lower = path.toLowerCase()
  if (lower.endsWith('.md')) return '📝'
  if (lower.endsWith('.echarts.json')) return '📊'
  if (lower.endsWith('.csv')) return '📋'
  if (lower.endsWith('.json') || lower.endsWith('.yaml')) return '🧾'
  if (/\.(png|jpe?g|gif|svg)$/.test(lower)) return '🖼'
  return '📄'
}
</script>

<style scoped>
.artifact-browser {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: white;
}
.browser-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  border-bottom: 1px solid #e9ecef;
  background: #f8f9fa;
}
.header-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 15px;
}
.header-icon { font-size: 20px; }
.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.btn-zip {
  font-size: 13px;
  color: #2f54eb;
  text-decoration: none;
  padding: 6px 12px;
  border: 1px solid #adc6ff;
  border-radius: 6px;
  background: white;
}
.btn-zip:hover { background: #e8f0ff; }
.btn-close {
  font-size: 24px;
  border: none;
  background: transparent;
  cursor: pointer;
  color: #8c8c8c;
  width: 32px;
  height: 32px;
  border-radius: 50%;
}
.btn-close:hover { background: #f1f3f5; color: #1a1a1a; }
.browser-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}
.file-tree {
  width: 260px;
  border-right: 1px solid #e9ecef;
  padding: 12px 8px;
  overflow-y: auto;
  background: #fafbfc;
  font-size: 13px;
}
.file-preview {
  flex: 1;
  overflow: auto;
  background: white;
}
.preview-empty {
  padding: 60px 20px;
  text-align: center;
  color: #8c8c8c;
}
:deep(.tree-dir-name), :deep(.tree-file) {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-radius: 4px;
  cursor: pointer;
  user-select: none;
}
:deep(.tree-dir-name:hover), :deep(.tree-file:hover) { background: #e8f0ff; }
:deep(.tree-selected) { background: #d6e4ff !important; color: #2f54eb; font-weight: 500; }
:deep(.tree-toggle) { width: 12px; font-size: 10px; }
:deep(.tree-icon) { font-size: 14px; }
:deep(.tree-children) { margin-left: 16px; }
:deep(.tree-name) { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
```

- [ ] **Step 2: build 验证**

Run: `cd D:/workplace/ads_data_agent/frontend && npm run build 2>&1 | tail -3`

Expected: `✓ built in ...ms`

- [ ] **Step 3: Commit**

```bash
cd D:/workplace/ads_data_agent
git add frontend/src/components/ArtifactBrowser.vue
git commit -m "feat(frontend): ArtifactBrowser with file tree + preview pane"
```

---

## Task 13: Chat.vue 集成 artifact_updated + 卡片 + 浏览弹层

**Files:**
- Modify: `frontend/src/pages/Chat.vue`

- [ ] **Step 1: 加 import + ref**

在 `<script setup>` import 区追加：

```js
import ArtifactCard from '../components/ArtifactCard.vue'
import ArtifactBrowser from '../components/ArtifactBrowser.vue'
```

在已有 ref 声明区追加：

```js
const browsingArtifactId = ref(null)
```

- [ ] **Step 2: 加 SSE handler**

在 `sseClient = new SSEClient(...)` 的 handlers 对象里，`plan:` handler 后追加：

```js
artifact_updated: (data) => {
  closeStreaming()
  closeThinking()
  messages.value.push({
    type: 'artifact',
    artifactId: data.artifact_id,
    action: data.action,
  })
  scrollToBottom()
},
```

- [ ] **Step 3: 模板渲染 artifact 消息**

在 `<template v-else-if="msg.type === 'thinking'">` 块**之前**追加：

```html
<ArtifactCard
  v-else-if="msg.type === 'artifact'"
  :artifact-id="msg.artifactId"
  :user-id="userId"
  @open="(id) => browsingArtifactId = id"
/>
```

- [ ] **Step 4: 模板根加浏览器全屏弹层**

在 `<ConfirmDialog ...>` 同级追加：

```html
<div v-if="browsingArtifactId" class="artifact-overlay" @click.self="browsingArtifactId = null">
  <div class="artifact-modal">
    <ArtifactBrowser
      :user-id="userId"
      :artifact-id="browsingArtifactId"
      @close="browsingArtifactId = null"
    />
  </div>
</div>
```

- [ ] **Step 5: 加弹层样式**

在 `<style scoped>` 内追加：

```css
.artifact-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.artifact-modal {
  width: 90vw;
  height: 90vh;
  max-width: 1400px;
  background: white;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.3);
}
```

- [ ] **Step 6: build 验证**

Run: `cd D:/workplace/ads_data_agent/frontend && npm run build 2>&1 | tail -3`

Expected: `✓ built in ...ms`

- [ ] **Step 7: Commit**

```bash
cd D:/workplace/ads_data_agent
git add frontend/src/pages/Chat.vue
git commit -m "feat(frontend): handle artifact_updated SSE + render card + browser overlay"
```

---

## Task 14: 独立工作区页面 Artifacts.vue + 路由

**Files:**
- Create: `frontend/src/pages/Artifacts.vue`
- Modify: `frontend/src/router/index.js`
- Modify: `frontend/src/pages/Chat.vue`

- [ ] **Step 1: 写 Artifacts.vue**

```vue
<!-- frontend/src/pages/Artifacts.vue -->
<template>
  <div class="workspace-page">
    <header class="topbar">
      <span class="brand">📦 我的工作区</span>
      <span class="user-tag">{{ userId }}</span>
      <button class="btn-back" @click="$router.push('/chat')">← 返回对话</button>
    </header>

    <main class="workspace-main">
      <div v-if="loading" class="loading">加载中...</div>
      <div v-else-if="!artifacts.length" class="empty">
        <div class="empty-icon">📂</div>
        <div>还没有任何 artifact</div>
        <div class="empty-hint">让 Agent 帮你生成第一份分析报告吧</div>
      </div>
      <div v-else class="artifact-grid">
        <div
          v-for="art in artifacts"
          :key="art.artifact_id"
          class="artifact-tile"
          @click="browsing = art.artifact_id"
        >
          <div class="tile-title">{{ art.title || art.artifact_id }}</div>
          <div class="tile-meta">
            {{ formatType(art.artifact_type) }} · {{ art.file_count }} 个文件 · {{ formatTime(art.created_at) }}
          </div>
          <div v-if="art.description" class="tile-desc">{{ art.description }}</div>
          <div class="tile-actions">
            <button class="tile-btn-delete" @click.stop="onDelete(art.artifact_id)">删除</button>
          </div>
        </div>
      </div>
    </main>

    <div v-if="browsing" class="overlay" @click.self="browsing = null">
      <div class="modal">
        <ArtifactBrowser :user-id="userId" :artifact-id="browsing" @close="browsing = null" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import ArtifactBrowser from '../components/ArtifactBrowser.vue'

const userId = localStorage.getItem('user_id') || 'anon'
const artifacts = ref([])
const loading = ref(true)
const browsing = ref(null)

async function load() {
  loading.value = true
  try {
    const resp = await fetch(`/api/artifacts/${userId}`)
    const body = await resp.json()
    artifacts.value = body.artifacts || []
  } finally {
    loading.value = false
  }
}

async function onDelete(id) {
  if (!confirm(`确定删除 ${id}？`)) return
  await fetch(`/api/artifacts/${userId}/${id}`, { method: 'DELETE' })
  await load()
}

const TYPE_LABEL = {
  report: '📊 综合报告',
  document: '📄 文档',
  spreadsheet: '📋 电子表格',
  presentation: '📽 演示',
  dataset: '🗃 数据集',
  mixed: '📁 混合产物',
}
function formatType(t) { return TYPE_LABEL[t] || ('📁 ' + (t || 'unknown')) }
function formatTime(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

onMounted(load)
</script>

<style scoped>
.workspace-page {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
}
.topbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 24px;
  height: 60px;
  background: linear-gradient(135deg, #c7000b 0%, #d81a24 100%);
  color: white;
}
.brand { font-size: 18px; font-weight: 600; flex: 1; }
.user-tag {
  font-size: 13px;
  padding: 4px 12px;
  background: rgba(255, 255, 255, 0.15);
  border-radius: 12px;
}
.btn-back {
  padding: 6px 16px;
  background: rgba(255, 255, 255, 0.18);
  border: 1px solid rgba(255, 255, 255, 0.3);
  color: white;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
}
.workspace-main {
  flex: 1;
  padding: 32px 24px;
  overflow-y: auto;
}
.loading, .empty {
  text-align: center;
  padding: 80px 20px;
  color: #8c8c8c;
}
.empty-icon { font-size: 64px; margin-bottom: 12px; }
.empty-hint { font-size: 13px; margin-top: 6px; color: #adb5bd; }
.artifact-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 16px;
  max-width: 1200px;
  margin: 0 auto;
}
.artifact-tile {
  background: white;
  border: 1px solid #e9ecef;
  border-radius: 12px;
  padding: 16px;
  cursor: pointer;
  transition: all 0.2s;
  position: relative;
}
.artifact-tile:hover {
  border-color: #adc6ff;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  transform: translateY(-2px);
}
.tile-title { font-weight: 600; font-size: 14px; color: #1a1a1a; margin-bottom: 6px; }
.tile-meta { font-size: 11px; color: #8c8c8c; margin-bottom: 8px; }
.tile-desc { font-size: 12px; color: #595959; line-height: 1.5; }
.tile-actions { margin-top: 12px; text-align: right; }
.tile-btn-delete {
  font-size: 11px;
  padding: 4px 10px;
  background: transparent;
  border: 1px solid #ffccc7;
  color: #c7000b;
  border-radius: 4px;
  cursor: pointer;
}
.tile-btn-delete:hover { background: #fff1f0; }
.overlay {
  position: fixed; inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex; align-items: center; justify-content: center;
  z-index: 1000;
}
.modal {
  width: 90vw; height: 90vh; max-width: 1400px;
  background: white; border-radius: 12px; overflow: hidden;
}
</style>
```

- [ ] **Step 2: 加路由**

修改 `frontend/src/router/index.js`，import 区追加：

```js
import Artifacts from '../pages/Artifacts.vue'
```

routes 数组追加：

```js
{ path: '/artifacts', component: Artifacts, meta: { requiresAuth: true } },
```

- [ ] **Step 3: Chat.vue 顶栏加入口**

修改 `frontend/src/pages/Chat.vue`，找到顶栏：

```html
<header class="topbar">
  <span class="brand">华为广告数据助手</span>
  <span class="user-tag">{{ userId }}</span>
  <button class="btn-new" @click="clearChat">新对话</button>
  <button class="btn-logout" @click="logout">退出</button>
</header>
```

在"新对话"按钮前加：

```html
  <button class="btn-new" @click="$router.push('/artifacts')">📦 我的工作区</button>
```

- [ ] **Step 4: build 验证**

Run: `cd D:/workplace/ads_data_agent/frontend && npm run build 2>&1 | tail -3`

Expected: `✓ built in ...ms`

- [ ] **Step 5: Commit**

```bash
cd D:/workplace/ads_data_agent
git add frontend/src/pages/Artifacts.vue frontend/src/router/index.js frontend/src/pages/Chat.vue
git commit -m "feat(frontend): standalone /artifacts workspace page + entry link"
```

---

## Task 15: E2E demo skill

**Files:**
- Create: `skills/demo-artifact-writer/SKILL.md`
- Create: `skills/demo-artifact-writer/package.json`
- Create: `skills/demo-artifact-writer/bin/write-demo-report.py`

- [ ] **Step 1: SKILL.md**

```markdown
---
name: demo-artifact-writer
description: 演示用：生成一份带数据 + 图表 + 洞察的示例 artifact
---

## 用法

```
write-demo-report --title "<报告标题>"
```

写一份 artifact 到 `$ADS_AGENT_ARTIFACT_DIR`，含 INDEX.md / manifest.yaml / insights/summary.md / charts/sample.echarts.json / data/sample.csv，最后向 stderr 输出 `<<<ADS_AGENT:ARTIFACT_UPDATED:artifact_id=$ADS_AGENT_ARTIFACT_ID>>>`。
```

- [ ] **Step 2: package.json**

```json
{
  "name": "demo-artifact-writer",
  "bin": {
    "write-demo-report": "bin/write-demo-report.py"
  }
}
```

- [ ] **Step 3: bin/write-demo-report.py**

```python
#!/usr/bin/env python
"""演示 skill：写一份 artifact 端到端验证用。"""
import argparse
import json
import os
import pathlib
import sys
from datetime import datetime

import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", default="示例分析报告")
    args = parser.parse_args()

    artifact_dir = os.environ.get("ADS_AGENT_ARTIFACT_DIR")
    artifact_id = os.environ.get("ADS_AGENT_ARTIFACT_ID")
    user_id = os.environ.get("ADS_AGENT_USER_ID")

    if not artifact_dir or not artifact_id:
        print("Error: 缺 ADS_AGENT_ARTIFACT_DIR / ADS_AGENT_ARTIFACT_ID 环境变量")
        sys.exit(1)

    root = pathlib.Path(artifact_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / "insights").mkdir(exist_ok=True)
    (root / "charts").mkdir(exist_ok=True)
    (root / "data").mkdir(exist_ok=True)

    (root / "INDEX.md").write_text(
        f"""# {args.title}

本份 artifact 包含：
- `insights/summary.md` 执行摘要
- `charts/sample.echarts.json` 趋势折线图
- `data/sample.csv` 原始数据（10 行）

---

> 这份是 demo-artifact-writer 生成的示例 artifact。
""",
        encoding="utf-8",
    )

    (root / "insights" / "summary.md").write_text(
        """## 执行摘要

- 关键发现 1：示例数据的趋势呈现稳步上升
- 关键发现 2：周末数据波动较大
- 建议：进一步分析周末波动原因
""",
        encoding="utf-8",
    )

    chart_config = {
        "title": {"text": "示例趋势"},
        "xAxis": {"type": "category", "data": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]},
        "yAxis": {"type": "value"},
        "series": [{
            "name": "数据",
            "type": "line",
            "data": [120, 132, 145, 167, 189, 220, 198],
            "smooth": True,
        }],
    }
    (root / "charts" / "sample.echarts.json").write_text(
        json.dumps(chart_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    (root / "data" / "sample.csv").write_text(
        "day,value,note\nMon,120,常规\nTue,132,常规\nWed,145,常规\nThu,167,常规\nFri,189,常规\nSat,220,周末高峰\nSun,198,周末\n",
        encoding="utf-8",
    )

    manifest = {
        "artifact_id": artifact_id,
        "title": args.title,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "user_id": user_id or "unknown",
        "artifact_type": "report",
        "description": "demo-artifact-writer 生成的示例分析报告，含摘要 + 折线图 + 数据表。",
        "entry_files": ["INDEX.md"],
        "related_skills": ["demo-artifact-writer"],
    }
    (root / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    sys.stderr.write(f"<<<ADS_AGENT:ARTIFACT_UPDATED:artifact_id={artifact_id}>>>\n")
    sys.stderr.flush()

    print(f"已生成 artifact：{artifact_id}（含 5 个文件，主要看 INDEX.md）")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 验证 skill 加载**

```bash
cd D:/workplace/ads_data_agent
.venv/Scripts/python.exe -c "
from agent.skill_loader import load_md_skills
pkg = load_md_skills('./skills', None)
print('summaries:', [s['name'] for s in pkg.summaries])
print('commands:', [s['commands'] for s in pkg.summaries])
"
```

Expected: 输出含 `demo-artifact-writer` + 命令 `['write-demo-report']`

- [ ] **Step 5: Commit**

```bash
git add skills/demo-artifact-writer/
git commit -m "feat(skills): demo-artifact-writer for artifact e2e validation"
```

---

## Task 16: 端到端冒烟（手动）

> 这是验证用 task，没有自动化测试。

- [ ] **Step 1: 启后端**

```bash
cd D:/workplace/ads_data_agent
.venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

- [ ] **Step 2: 启前端**

```bash
cd D:/workplace/ads_data_agent/frontend
npm run dev
```

- [ ] **Step 3: 浏览器测试**

1. 访问 `http://localhost:5173`
2. 登录 `e2e-test`
3. 发送：「调 demo-artifact-writer 的 write-demo-report 子命令，title 用"端到端测试"」
4. 期望：
   - 思考流显示工具调用
   - 对话流插入 artifact 卡片
   - 点卡片 → 弹层打开 ArtifactBrowser
   - 文件树：manifest.yaml / INDEX.md / insights/ / charts/ / data/
   - INDEX.md 默认渲染
   - charts/sample.echarts.json 显示折线图
   - data/sample.csv 表格化
   - ⬇下载 zip 触发 .zip 下载
   - 顶栏「📦 我的工作区」打开列表
5. 删除 → tile 消失，目录被清

- [ ] **Step 4: 写验证记录**

```bash
echo "$(date -Iseconds) e2e-test PASSED — Task 16" >> .e2e-validation-log
git add .e2e-validation-log
git commit -m "chore: artifact e2e validation passed"
```

---

## Task 17: 文档：02-features/13-artifact-system.md

**Files:**
- Create: `docs/02-features/13-artifact-system.md`

- [ ] **Step 1: 写 feature 文档**

```markdown
# 13 Artifact 系统（持久化大型分析产物）

> **前置**：先读 [01-skill-tool-channel-model.md](./01-skill-tool-channel-model.md)。

## 一、为什么需要

数据分析的真实输出经常超出 SSE 文本流的承载能力——多视角数据、多张图、完整洞察、Office 文件等。Artifact 系统让 skill 把这些大产物**持久化为用户长期资产**，跨 channel 差异化呈现。

详细动机见 [spec](../superpowers/specs/2026-04-30-artifact-system-design.md)。

## 二、核心概念

| 概念 | 含义 |
|---|---|
| **Artifact** | 一份分析产物，物理上 = 一个目录 + 一份 manifest.yaml |
| **Workspace** | per-user 的 artifact 集合（跨 conversation 持久共享） |
| **Manifest** | 强制契约文件，含 artifact 类型 / 标题 / 入口文件 / 元数据 |
| **immutable** | artifact 写完不变（同 id 后写覆盖前写） |

## 三、Skill 怎么写产物

runner 在每次工具调用前注入 env：
- `ADS_AGENT_ARTIFACT_DIR` — 候选 artifact 目录
- `ADS_AGENT_ARTIFACT_ID`  — 候选 id
- `ADS_AGENT_USER_ID`      — 用户标识

skill 自由写文件到 `ADS_AGENT_ARTIFACT_DIR`，写完发约定通知行（**只在 stderr**）：

```
<<<ADS_AGENT:ARTIFACT_UPDATED:artifact_id=<id>>>>
```

参考实现：`skills/demo-artifact-writer/bin/write-demo-report.py`。

## 四、Channel 呈现差异

| Channel | 呈现 |
|---|---|
| `WebSSEChannel` | 对话流插入 ArtifactCard，点击展开 ArtifactBrowser |
| `CLIChannel`    | 打印简短通知 |
| 未来 Mobile     | zip 下载 / 服务端 PDF 转换 |

## 五、独立工作区页面

`/artifacts` 路由（顶栏入口"📦 我的工作区"）—— 独立访问 artifact 列表。

## 六、Manifest schema

详见 [03-api-reference/data-contracts.md](../03-api-reference/data-contracts.md)。

## 七、关联源码

- 模型：`agent/artifact.py`
- REST API：`api/artifacts.py`
- skill_loader 集成：`agent/skill_loader.py::_extract_artifact_ids` / `consume_pending_artifact_ids`
- runner 集成：`api/channel/runner.py`
- 前端：`frontend/src/components/ArtifactCard.vue` + `ArtifactBrowser.vue` + `preview/*.vue` + `pages/Artifacts.vue`

## 设计决策记录（ADR）

### ADR-019：Artifact 内容**不进 LLM context**

**Context**：让 LLM 直接看 artifact 内容会导致 token 飙升 + checkpoint 膨胀。

**Decision**：artifact 内容只存文件系统；LLM 只看到工具返回的简短摘要。LLM 在最终 markdown 里**引用** artifact 名而不是内容。

**Consequences**：✅ token 省、checkpoint 干净；⚠️ LLM 不能读完整报告再总结——靠 skill 写的 INDEX.md 给用户导览。

### ADR-020：manifest 是契约，目录结构自由

**Context**：固定子目录只适合"分析报告"形态，无法承载 PPT/Excel/Word 等单文件交付。

**Decision**：仅 `manifest.yaml` 强制；artifact_type + entry_files 让前端选合适的渲染策略。

**Consequences**：✅ 灵活；⚠️ skill 作者要正确写 manifest。

### ADR-021：skill stderr 通知 + 前端按需拉

**Context**：runner 扫目录方案有耦合。

**Decision**：skill 主动通过 stderr 约定行通知，runner 转发为 SSE 事件，前端按需调 manifest API 拉详情。

**Consequences**：✅ 解耦、支持多次写、schema 漂移容忍；⚠️ skill 必须记得发通知。
```

- [ ] **Step 2: 更新 docs/README.md 索引**

修改 `docs/README.md` 的 02-features 表格末尾追加：

```markdown
| [13-artifact-system](02-features/13-artifact-system.md) | Artifact 系统（持久化大型分析产物） |
```

- [ ] **Step 3: Commit**

```bash
git add docs/02-features/13-artifact-system.md docs/README.md
git commit -m "docs(features): add 13-artifact-system feature document"
```

---

## Task 18: 文档：API reference + SSE event types + known-issues

**Files:**
- Modify: `docs/03-api-reference/rest-endpoints.md`
- Modify: `docs/03-api-reference/sse-event-types.md`
- Modify: `docs/03-api-reference/data-contracts.md`
- Modify: `docs/05-known-issues-and-roadmap/README.md`
- Modify: `docs/05-known-issues-and-roadmap/roadmap.md`

- [ ] **Step 1: rest-endpoints 加 artifact endpoints 段**

在 `docs/03-api-reference/rest-endpoints.md` 末尾"关联源码"段之前追加：

```markdown
## 9. Artifact 相关

### GET `/api/artifacts/{user_id}` — 列出用户 artifact

返摘要列表，按 created_at 倒序。

### GET `/api/artifacts/{user_id}/{artifact_id}/manifest`

返 manifest + 完整目录树。

### GET `/api/artifacts/{user_id}/{artifact_id}/file/{relative_path}`

拿单文件。404 / 400（路径穿越）。

### GET `/api/artifacts/{user_id}/{artifact_id}/zip`

整包 zip 下载。

### DELETE `/api/artifacts/{user_id}/{artifact_id}`

删除 artifact。

详见 [02-features/13-artifact-system.md](../02-features/13-artifact-system.md)。
```

- [ ] **Step 2: sse-event-types 加 artifact_updated**

在事件清单表追加：

```markdown
| `artifact_updated` | skill stderr 约定行通知，runner 转发 | `{"artifact_id": str, "action": "created" \| "updated"}` |
```

文末追加专门段落：

```markdown
## artifact_updated

skill 在执行过程中创建 / 更新 artifact 时通过此事件通知前端。

**Schema**：
```json
{"artifact_id": "2026-04-30-093015-report", "action": "created"}
```

详见 [02-features/13-artifact-system.md](../02-features/13-artifact-system.md)。
```

- [ ] **Step 3: data-contracts 加 manifest schema**

末尾追加：

```markdown
## 七、Artifact Manifest schema

```yaml
artifact_id: 2026-04-30-093015-report
title: <人可读标题>
created_at: 2026-04-30T09:30:15+08:00
user_id: <user_id>
artifact_type: report | document | spreadsheet | presentation | dataset | mixed
description: |
  2-3 行简要说明
entry_files:
  - INDEX.md
related_skills:
  - skill-name-1
related_conversation_id: <可选>
```
```

- [ ] **Step 4: known-issues 追加新条目**

在 `docs/05-known-issues-and-roadmap/README.md` "B. Stub 待实现" 段末尾追加：

```markdown
### B5. Artifact v2 扩展未实现

- 多 artifact 合并 / 重组 / 重命名 / 移动 / 共享
- 跨用户分享
- 自动 retention（90 天归档 / 删除）
- artifact 全文搜索
- "项目 / collection" 概念
- Office 文件内嵌预览（pptx / docx / xlsx）

均不阻塞 v1 核心功能，按业务诉求优先级再做。
```

在 `docs/05-known-issues-and-roadmap/roadmap.md` P2 段末尾追加：

```markdown
### P2.11 — artifact-v2: 共享 / 整理 / 搜索

- **背景**：v1 artifact 是 immutable + 扁平列表
- **范围**：collection 概念 + rename/move + 全文搜索 + retention policy
- **触发条件**：用户工作区累积 ≥50 份 artifact 时
```

- [ ] **Step 5: Commit**

```bash
git add docs/03-api-reference/ docs/05-known-issues-and-roadmap/
git commit -m "docs: artifact endpoints + SSE event + manifest schema + known-issues"
```

---

## 自审：Spec Coverage

| Spec 节 | 实施 task |
|---|---|
| ① 存储位置 `data/{user_id}/artifacts/` | Task 4 |
| ② artifact_id 命名 | Task 1 + Task 9 |
| ③ Manifest 契约 + 目录自由 | Task 1 + Task 2 |
| ④ env 注入 + 约定目录 | Task 9 + Task 7 |
| ⑤ stderr 通知 + 前端按需拉 | Task 7 + Task 8 |
| ⑥ Channel 呈现 | Task 6 + Task 13 |
| ⑦ REST API 5 个 | Task 5 |
| ⑧ v1 不自动清理 | （implicit） |
| ⑨ 路径校验 / 安全 | Task 3 + Task 5 |
| ⑩ SSE event schema | Task 6 + Task 8 |
| 前端 ArtifactBrowser | Task 12 |
| 前端独立工作区 | Task 14 |
| E2E demo skill | Task 15 + Task 16 |
| 文档更新 | Task 17 + Task 18 |

✅ 全覆盖。

---

## 完工验证

实施完所有 task 后跑：

```bash
cd D:/workplace/ads_data_agent
.venv/Scripts/python.exe -m pytest tests/ -v
cd frontend && npm run build 2>&1 | tail -5
```

Expected:
- pytest 60+ 通过（原 42 + artifact 模型 ~12 + REST endpoints ~11 + skill_loader ~5 + runner ~2）
- frontend build 成功

---

## 文档历史

- 2026-04-30：初版（chiky0123 + Claude，writing-plans skill）
