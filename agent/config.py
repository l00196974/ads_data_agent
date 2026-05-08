import os
import yaml
from pathlib import Path
from pydantic import BaseModel, model_validator
from dotenv import load_dotenv

load_dotenv()


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = []


class LongContextConfig(BaseModel):
    """长上下文场景的内存压缩与截断策略。

    当单个 thread 的对话历史增长到一定规模后，必须自动压缩，
    否则每次 LLM 调用都全量 prefill 历史，token 成本和延迟会随轮数线性飙升。

    所有字段都可以在 config.yaml 的 `agent.long_context` 段覆盖。
    """

    # 单 thread 累计 token 数超过此阈值时，自动触发 SummarizationMiddleware。
    # 默认 80000 留出余量给 prefill + 当前轮的 output。
    # 适当下调可以在更小模型（32k context）上提前压缩；上调适合大 context 模型。
    summarization_trigger_tokens: int = 80000

    # 压缩后保留最近 N 条消息原文，更早的会被合并为摘要。
    # 太小（< 6）容易丢工具调用上下文（plan / 上一次工具结果）；
    # 太大（> 30）压缩效果有限。
    summarization_keep_messages: int = 10

    # 单条业务工具返回值字节数超过此值时，messages 里只保留摘要 + 文件路径，
    # 完整内容写到 data/{user_id}/tool_outputs/{conv_id}/{tool_call_id}.json。
    tool_output_max_bytes: int = 5000

    # 卸盘文件 TTL（天）。Backend 启动时清理 mtime 超龄的 tool_outputs。
    # SummarizationMiddleware 把老 messages 替换成 summary string 后，指针消失但文件
    # 残留——靠 TTL 兜底。0 = 关闭清理（开发期想保留完整原始数据时用）。
    tool_output_ttl_days: int = 30


class SubAgentSpec(BaseModel):
    """单个子 Agent 的声明（透传给 deepagents.SubAgent TypedDict）。

    每个子 Agent 拥有独立 context window：主 Agent 调 `task` 工具委派任务给子 Agent，
    子 Agent 跑完只把最终 ToolMessage 内容回主 Agent，中间过程不污染主上下文。
    这是缓解长上下文问题的"主方案"——`SummarizationMiddleware` 是兜底。

    可选字段（model）省略时继承主 Agent 配置；tools 默认继承主 Agent 的工具集。
    """

    name: str                  # 唯一标识，主 Agent 用此名调用 task()
    description: str           # 子 Agent 职责，主 Agent 据此决定何时委派
    system_prompt: str         # 子 Agent 自己的角色 prompt
    model: str | None = None   # 可选，"provider:model" 格式覆盖主 Agent 模型


class InterruptConfig(BaseModel):
    """HitL 拦截规则，按风险分级。

    - **high_risk**：强制弹窗确认，**忽略**用户的免确认白名单。用于不可逆 / 可观测影响大的
      操作（如改投放预算、删广告组、暂停投放）。即使用户某次勾选"以后不再问"，下次还是要确认。
    - **medium_risk**：默认弹窗确认，但用户可在弹窗里勾"以后不再确认 [工具名]"加入全局
      白名单（`data/auto_approve.yaml`）。用于"不可逆但不致命"的动作（创建人群、创建临时表、
      生成报告 artifact、导出大数据）。

    向后兼容：旧 config 用 `interrupt_on: [tool1, tool2]` 这种 list 形式的也会被接受，
    自动归到 medium_risk —— 因为旧配置语义是"敏感但默认可放行"，等同 medium。
    新配置写 dict 形式即可启用分级 + 免确认白名单能力。
    """
    high_risk: list[str] = []
    medium_risk: list[str] = []

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_list(cls, v):
        # 旧形式：interrupt_on: [tool1, tool2]  →  全归 medium_risk
        if isinstance(v, list):
            return {"high_risk": [], "medium_risk": v}
        return v

    def all_intercepted(self) -> list[str]:
        """所有被拦截的工具名（用于传给 deepagents middleware）。"""
        return list(self.high_risk) + list(self.medium_risk)

    def classify(self, tool_name: str) -> str:
        """返回 'high' | 'medium' | 'none'。"""
        if tool_name in self.high_risk:
            return "high"
        if tool_name in self.medium_risk:
            return "medium"
        return "none"


class AgentConfig(BaseModel):
    max_iterations: int = 20  # legacy 字段，未被任何代码消费——保留以免破坏旧 config.yaml
    timeout_seconds: int = 120
    # LangGraph recursion_limit：单轮对话的 graph step 上限（每个 LLM 调用 / 工具调用 ≈ 1 step）。
    # 默认 150 覆盖大多数数据诊断场景（多维下钻 + 对比分析 + 人群画像 ≈ 50-80 steps）。
    # 提到太高（>300）通常是死循环——遇到撞 limit 应当先看 messages 而不是无脑加大。
    recursion_limit: int = 150
    # Runner 实现——P4b 切默认到 v2（去 framework 重构完成）。
    #   v2: 纯 Python 链路（runner_v2 + agent.loop + agent.state）——默认
    #   v1: 历史 deepagents/langgraph 链路 —— **已删除**，仅保留枚举值兼容旧 config.yaml
    # 旧 config 写 v1 仍按 v2 跑（chat.py 内 v1 代码已无）。
    runner_mode: str = "v2"
    interrupt_on: InterruptConfig = InterruptConfig()
    long_context: LongContextConfig = LongContextConfig()
    subagents: list[SubAgentSpec] = []  # 主 Agent 可派工的子 Agent 列表


class LLMConfig(BaseModel):
    """LLM 配置——只走 OpenAI 兼容协议（ChatOpenAI）。

    `provider` 字段保留兼容旧 config.yaml / .env，但**不再影响实际行为**——
    所有调用都走 ChatOpenAI；切换不同后端用 `base_url`。
    """
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    api_key: str = ""
    base_url: str = ""


class PersistenceConfig(BaseModel):
    type: str = "local"
    data_dir: str = "./data"
    # Store 后端选择：deepagents 内置工具的持久化层。
    #   memory: InMemoryStore，进程内 dict（重启即丢，多副本失同步）
    #   sqlite: AsyncSqliteStore（默认），落 data/store.db
    #   mysql:  未实现 stub
    store_backend: str = "sqlite"


class SkillsConfig(BaseModel):
    """业务技能加载配置。

    md_dir: 扫描这个目录下所有子目录，包含 SKILL.md 的会被解析成 langchain tool。
    每个子目录是一个 skill 包：SKILL.md + package.json::bin + bin/*.js|*.py|*.sh。
    框架不内置业务技能，由部署方按规范放入。
    """

    md_dir: str = "./skills"


class AppConfig(BaseModel):
    server: ServerConfig = ServerConfig()
    agent: AgentConfig = AgentConfig()
    llm: LLMConfig = LLMConfig()
    persistence: PersistenceConfig = PersistenceConfig()
    skills: SkillsConfig = SkillsConfig()


def load_config(path: str = "config.yaml") -> AppConfig:
    raw = {}
    if Path(path).exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}

    raw["llm"] = {
        "provider": os.getenv("LLM_PROVIDER", "openai"),
        "model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
        "api_key": os.getenv("LLM_API_KEY", ""),
        "base_url": os.getenv("LLM_BASE_URL", ""),
    }
    return AppConfig(**raw)
