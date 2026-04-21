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


class AgentConfig(BaseModel):
    max_iterations: int = 20
    timeout_seconds: int = 120
    interrupt_on: list[str] = []


class LLMConfig(BaseModel):
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    api_key: str = ""


class PersistenceConfig(BaseModel):
    type: str = "local"
    data_dir: str = "./data"


class AppConfig(BaseModel):
    server: ServerConfig = ServerConfig()
    agent: AgentConfig = AgentConfig()
    llm: LLMConfig = LLMConfig()
    persistence: PersistenceConfig = PersistenceConfig()


def load_config(path: str = "config.yaml") -> AppConfig:
    raw = {}
    if Path(path).exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}

    raw["llm"] = {
        "provider": os.getenv("LLM_PROVIDER", "anthropic"),
        "model": os.getenv("LLM_MODEL", "claude-sonnet-4-6"),
        "api_key": os.getenv("ANTHROPIC_API_KEY", ""),
    }
    return AppConfig(**raw)
