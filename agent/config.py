import os
import yaml
from pathlib import Path
from pydantic import BaseModel
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
    # 完整内容写到 data/{user_id}/tool_outputs/{tool_call_id}.json（暂未实现）。
    tool_output_max_bytes: int = 5000


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


class AgentConfig(BaseModel):
    max_iterations: int = 20
    timeout_seconds: int = 120
    interrupt_on: list[str] = []
    long_context: LongContextConfig = LongContextConfig()
    subagents: list[SubAgentSpec] = []  # 主 Agent 可派工的子 Agent 列表


class LLMConfig(BaseModel):
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    api_key: str = ""
    base_url: str = ""


class PersistenceConfig(BaseModel):
    type: str = "local"
    data_dir: str = "./data"


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
        "provider": os.getenv("LLM_PROVIDER", "anthropic"),
        "model": os.getenv("LLM_MODEL", "claude-sonnet-4-6"),
        "api_key": os.getenv("LLM_API_KEY", os.getenv("ANTHROPIC_API_KEY", "")),
        "base_url": os.getenv("LLM_BASE_URL", ""),
    }
    return AppConfig(**raw)
