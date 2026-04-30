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
    if "/" in user_id or "\\" in user_id or ".." in user_id:
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
