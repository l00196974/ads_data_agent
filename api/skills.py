"""Skill listing and per-user skill upload."""
import io
import shutil
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from agent.config import load_config
from agent.skill_loader import load_md_skills
from agent.user_space import UserSpace
from api.models import SkillCreateRequest

router = APIRouter()
cfg = load_config()

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB


@router.get("/{user_id}")
async def list_skills(user_id: str):
    us = UserSpace(user_id, cfg.persistence.data_dir)
    pkg = load_md_skills(cfg.skills.md_dir, us.skills_dir)
    system_md = [s for s in pkg.summaries if s.get("source") == "system"]
    user_md = [s for s in pkg.summaries if s.get("source") == "user"]
    return {"system": system_md, "user": user_md}


@router.post("/{user_id}/upload")
async def upload_skill(user_id: str, file: UploadFile = File(...)):
    """上传 SKILL.md 风格的技能包（zip 格式）。

    zip 内容应包含**单个顶层目录**，目录里有 SKILL.md。例如：
        my-skill/
          SKILL.md
          package.json
          bin/...

    解压到 data/{user_id}/skills/{my-skill}/，下次 chat 该用户的 agent 会自动看到这个 skill。
    """
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "请上传 .zip 文件")

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB")

    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(400, "无效的 zip 文件")

    namelist = zf.namelist()
    if not namelist:
        raise HTTPException(400, "zip 是空的")

    # 安全：禁止路径穿越和绝对路径
    for name in namelist:
        if name.startswith("/") or ".." in Path(name).parts:
            raise HTTPException(400, f"非法路径：{name}")

    # 校验：zip 必须有单一顶层目录
    top_components = set()
    for name in namelist:
        parts = name.split("/")
        if parts[0]:
            top_components.add(parts[0])
    if len(top_components) != 1:
        raise HTTPException(
            400, f"zip 应包含单一顶层目录，但发现 {len(top_components)} 个"
        )
    skill_dir_name = top_components.pop()

    # 校验：顶层目录里必须有 SKILL.md
    skill_md_entry = f"{skill_dir_name}/SKILL.md"
    if skill_md_entry not in namelist:
        raise HTTPException(400, f"找不到 {skill_md_entry}")

    # 解压前清理同名目录（用户上传等价于覆盖）
    us = UserSpace(user_id, cfg.persistence.data_dir)
    target = us.skills_dir / skill_dir_name
    if target.exists():
        shutil.rmtree(target)

    zf.extractall(us.skills_dir)
    zf.close()

    return {"status": "uploaded", "name": skill_dir_name}


@router.delete("/{user_id}/{skill_name}")
async def delete_skill(user_id: str, skill_name: str):
    """删除用户上传的某个 skill 目录。"""
    if "/" in skill_name or ".." in skill_name:
        raise HTTPException(400, "非法 skill_name")
    us = UserSpace(user_id, cfg.persistence.data_dir)
    target = us.skills_dir / skill_name
    if not target.exists() or not target.is_dir():
        raise HTTPException(404, "skill 不存在")
    shutil.rmtree(target)
    return {"status": "deleted", "name": skill_name}


@router.post("/{user_id}")
async def create_skill(user_id: str, req: SkillCreateRequest):
    """[遗留] 通过 JSON 提交一段 Python 代码作为 user skill。

    实际上 user .py skill 当前没有被 agent 加载（loader 只识别 SKILL.md 包）。
    保留此 endpoint 仅为向后兼容，后续可移除。要加技能请用 /upload。
    """
    us = UserSpace(user_id, cfg.persistence.data_dir)
    skill_file = us.skills_dir / f"{req.name}.py"
    skill_file.write_text(f'"""{req.description}"""\n\n{req.code}', encoding="utf-8")
    return {"status": "created", "name": req.name}
