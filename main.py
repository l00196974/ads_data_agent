import asyncio
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
from agent.wisedata_token_refresher import start_refresher
from agent.config import load_config
from agent.log_setup import init_logging
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


async def _warmup_for_first_request() -> None:
    """启动期预热：把首次请求的"懒加载"工作前置到 backend 启动阶段。

    1. **tiktoken encoder**：BPE 文件读取 + cache 初始化（约 100-300ms）
    2. **load_md_skills**：CSV 解析 + npm install（首次 cold install 可能 30s+；
       前置不增总耗时，UX 改善是用户启动时不操作）
    3. **ThreadStore + AgentLoop 装配**：openai client / 中间件链实例化一次

    失败不阻塞 backend 启动——预热只是优化。
    """
    import logging
    log = logging.getLogger("agent.warmup")

    # 1. tiktoken encoder
    try:
        import tiktoken
        tiktoken.get_encoding("cl100k_base").encode("warmup")
        log.info("warmup: tiktoken encoder ready")
    except Exception as e:
        log.warning("warmup: tiktoken skipped: %s", e)

    # 2 + 3. skill 加载 + ThreadStore + 简单 loop 装配
    try:
        from agent.skill_loader import load_md_skills
        from agent.state import ThreadStore
        md_pkg = load_md_skills(cfg.skills.md_dir, user_dir=None)
        log.info("warmup: load_md_skills done (skills=%d)", len(md_pkg.summaries))

        # ThreadStore 单例 init（lifespan 期间持有 connection）——chat 模块的
        # _v2_store_cache 也认这个实例，避免后续首次请求再建一次
        from api import chat as chat_module
        if "store" not in chat_module._v2_store_cache:
            store = ThreadStore(Path(cfg.persistence.data_dir) / "checkpoints.db")
            await store.init()
            chat_module._v2_store_cache["store"] = store
        log.info("warmup: ThreadStore ready")
    except Exception as e:
        log.warning("warmup: skipped: %s", e, exc_info=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 首次请求预热——前置 tiktoken / skill / ThreadStore 的懒加载
    await _warmup_for_first_request()

    # 启动 wisedata token 后台自动刷新（每 5 分钟检查，过期前 15 分钟自动刷新）
    project_root = Path(__file__).parent
    refresh_task = await start_refresher(project_root)

    try:
        yield
    finally:
        # 停止 token 刷新后台任务
        if not refresh_task.done():
            refresh_task.cancel()
            try:
                await refresh_task
            except asyncio.CancelledError:
                pass

        # 关 ThreadStore（释放 aiosqlite connection）
        try:
            from api import chat as chat_module
            store = chat_module._v2_store_cache.pop("store", None)
            if store is not None:
                await store.close()
        except Exception:
            pass


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
