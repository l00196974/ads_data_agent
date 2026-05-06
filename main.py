from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agent import auto_approve
from agent.config import load_config
from agent.core import (
    close_checkpointer,
    close_store,
    init_checkpointer,
    init_store,
)
from agent.log_setup import init_logging
from agent.user_space import UserSpace
from api import artifacts, chat, skills

cfg = load_config()
init_logging(cfg.persistence.data_dir)
auto_approve.init(cfg.persistence.data_dir)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # AsyncSqliteSaver / AsyncSqliteStore 都依赖 aiosqlite，必须在事件循环里 init
    await init_checkpointer()
    await init_store()
    try:
        yield
    finally:
        await close_store()
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
app.include_router(artifacts.router, prefix="/api/artifacts", tags=["artifacts"])

# 前端静态文件（构建后）
frontend_dist = Path("frontend/dist")
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        return FileResponse(str(frontend_dist / "index.html"))
