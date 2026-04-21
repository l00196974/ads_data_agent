from fastapi import APIRouter
from api.models import SkillCreateRequest
from agent.user_space import UserSpace
from agent.config import load_config
from skills.system import SYSTEM_SKILLS

router = APIRouter()
cfg = load_config()


@router.get("/{user_id}")
async def list_skills(user_id: str):
    system = [
        {"name": f.__name__, "description": (f.__doc__ or "").strip(), "type": "system"}
        for f in SYSTEM_SKILLS
    ]
    us = UserSpace(user_id, cfg.persistence.data_dir)
    user = [
        {"name": f.stem, "description": "", "type": "user"}
        for f in us.skills_dir.glob("*.py")
    ]
    return {"system": system, "user": user}


@router.post("/{user_id}")
async def create_skill(user_id: str, req: SkillCreateRequest):
    us = UserSpace(user_id, cfg.persistence.data_dir)
    skill_file = us.skills_dir / f"{req.name}.py"
    skill_file.write_text(f'"""{req.description}"""\n\n{req.code}')
    return {"status": "created", "name": req.name}
