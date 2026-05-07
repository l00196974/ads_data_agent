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


async def _warmup_for_first_request() -> None:
    """启动期预热：把首次请求的"懒加载"工作前置到 backend 启动阶段。

    不消除 LLM 端的冷启动（厂商 autoscaler / TLS / prompt cache miss——这些必须真发
    HTTP 请求才能预热，不该在启动时打 LLM 钱包），但把以下纯本地的 init 工作前置：

    1. **tiktoken encoder**：BPE 文件读取 + cache 初始化（约 100-300ms）
    2. **load_md_skills**：CSV 解析 + npm install（首次 cold install 可能 30s+，
       但本来就要在首次 chat 时做，这里只是前置——总耗时不变，UX 改善是因为用户在
       启动时不操作）
    3. **build_agent 整套管道**：deepagents 中间件链 / ChatOpenAI client / ToolNode
       schema / Summarization 工厂 / IterationGuard 等都实例化一次（首次约 300-800ms）

    失败不阻塞 backend 启动——预热只是优化，本来就允许跳过。
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

    # 2 + 3. skill 加载 + agent 工厂（一气跑通，让 SKILL.md / CSV / 中间件链全 warm 起来）
    try:
        from agent.skill_loader import load_md_skills
        from agent.core import build_agent
        md_pkg = load_md_skills(cfg.skills.md_dir, user_dir=None)
        log.info("warmup: load_md_skills done (skills=%d)", len(md_pkg.summaries))

        # 走完 build_agent 整套管道——_build_model 创建 ChatOpenAI client 但**不发请求**，
        # create_deep_agent 实例化中间件链 + ToolNode schema 验证。agent 构建完丢弃。
        # user_id="__warmup__" 是占位，build_agent 内部不消费 user_id，无副作用（不创建目录）
        _ = build_agent(
            user_id="__warmup__",
            system_prompt="warmup",
            skills=list(md_pkg.tools),
            interrupt_on=cfg.agent.interrupt_on,
            cfg=cfg,
        )
        log.info("warmup: build_agent pipeline ready")
    except Exception as e:
        log.warning("warmup: agent factory skipped: %s", e, exc_info=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # AsyncSqliteSaver / AsyncSqliteStore 都依赖 aiosqlite，必须在事件循环里 init
    await init_checkpointer()
    await init_store()
    # 首次请求预热——前置 tiktoken / skill / agent 工厂的懒加载
    await _warmup_for_first_request()
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
