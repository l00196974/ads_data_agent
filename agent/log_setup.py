"""统一日志初始化。

两个 handler：
  - 文件 handler: data/logs/backend-YYYY-MM-DD.log，DEBUG 级别（项目自己 logger）
  - 控制台 handler: WARNING 级别（uvicorn / 第三方降噪）

按日期切分。同日重启复用同一文件（append）。

只配置项目自己的 logger（`agent.*` / `api.*`）为 DEBUG，
其他 3rd-party（openai / httpx / langgraph / langchain）保持 WARNING——
否则 DEBUG 会刷出每次 HTTP 请求体，和真正的诊断信号混一起没法看。
"""
from __future__ import annotations

import logging
import logging.handlers
from datetime import date
from pathlib import Path

_INITIALIZED = False


def init_logging(data_dir: str | Path = "./data") -> None:
    """幂等。重复调用是 no-op。"""
    global _INITIALIZED
    if _INITIALIZED:
        return

    logs_dir = Path(data_dir) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    log_file = logs_dir / f"backend-{today}.log"

    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d | %(levelname)-7s | %(name)-32s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # 文件 handler：DEBUG，所有项目 logger 写入
    fh = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=20 * 1024 * 1024,  # 20 MB / file
        backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    # 控制台 handler：WARNING，避免压控制台
    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(fmt)

    # 项目 logger（agent.* / api.*）：DEBUG，挂两个 handler
    for name in ("agent", "api"):
        log = logging.getLogger(name)
        log.setLevel(logging.DEBUG)
        log.addHandler(fh)
        log.addHandler(ch)
        log.propagate = False  # 别冒泡到 root，避免 uvicorn 把项目日志再打一遍

    # 第三方降噪（root 默认 WARNING 即可，但显式压一下噪源）
    for noisy in ("httpx", "openai", "httpcore", "langchain", "langgraph"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _INITIALIZED = True
    logging.getLogger("agent.log_setup").info(
        "logging initialized: file=%s, project_loggers=DEBUG, third_party=WARNING",
        log_file,
    )
