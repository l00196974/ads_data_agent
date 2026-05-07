import os
from contextlib import asynccontextmanager
from pathlib import Path

# tiktoken 离线 BPE 缓存：必须在 import tiktoken（间接经 langchain / runner.py）之前 set，
# 否则 tiktoken 已经决定走默认 cache 路径（%TMP%/data-gym-cache）就来不及了。
# 公司内网 / 离线环境拦 OpenAI BPE CDN 时，靠这个目录里的预下载文件保证 tiktoken 可用。
os.environ.setdefault(
    "TIKTOKEN_CACHE_DIR",
    str(Path(__file__).parent / "vendor" / "tiktoken_cache"),
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agent import (
    auto_approve,
    conversation_events,
    conversation_meta,
    conversation_metrics,
    tool_outputs_cleanup,
)
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
# 历史会话元数据 + 思考事件流持久化（与 checkpointer 共用 data/checkpoints.db）
conversation_meta.init(cfg.persistence.data_dir)
conversation_events.init(cfg.persistence.data_dir)
conversation_metrics.init(cfg.persistence.data_dir)
# 启动时清理超龄 tool_outputs 卸盘文件（兜底：SummarizationMiddleware 替换后
# 失联的孤立文件、软删未联动的残留）。0 天 = 关闭，开发场景用
tool_outputs_cleanup.cleanup_expired(
    cfg.persistence.data_dir,
    cfg.agent.long_context.tool_output_ttl_days,
)


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
