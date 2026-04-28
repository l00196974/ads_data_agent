from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agent.config import load_config
from agent.core import close_checkpointer, init_checkpointer
from agent.mock_supervisor import start_skill_mocks, stop_skill_mocks
from agent.user_space import UserSpace
from api import chat, skills

cfg = load_config()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # AsyncSqliteSaver 必须在事件循环里 init（aiosqlite 是 async）
    await init_checkpointer()
    # 起 SKILL.md 包里的 mock server——开发态本地调用真实 skill 不需要外部 API
    await start_skill_mocks(cfg.skills.md_dir)
    yield
    await stop_skill_mocks()
    await close_checkpointer()


app = FastAPI(title="Ads Data Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.server.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Session-Id", "X-Conversation-Id"],
)

app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(skills.router, prefix="/api/skills", tags=["skills"])

# 前端静态文件（构建后）
frontend_dist = Path("frontend/dist")
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        return FileResponse(str(frontend_dist / "index.html"))
