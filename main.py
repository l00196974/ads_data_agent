from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from agent.config import load_config
from api import chat, skills

cfg = load_config()

app = FastAPI(title="Ads Data Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.server.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
