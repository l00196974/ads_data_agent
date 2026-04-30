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
    # 用 URL 编码的 %2E%2E 绕过 TestClient URL 规范化，验证后端安全防护
    resp = client.get(f"/api/artifacts/alice/{aid}/file/%2E%2E/%2E%2E/%2E%2E/etc/passwd")
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


def test_user_id_rejects_backslash(client):
    """Windows 路径分隔符 \\ 不能绕过 user_id 校验。"""
    resp = client.get("/api/artifacts/alice%5Cbob")  # %5C = \
    assert resp.status_code == 400


def test_user_id_rejects_traversal(client):
    """user_id 含 .. 应 400。"""
    # 用 URL 编码确保 .. 进入 user_id 参数
    resp = client.get("/api/artifacts/%2E%2E")
    assert resp.status_code == 400
