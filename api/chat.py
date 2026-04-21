import json
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from api.models import ChatRequest, ConfirmRequest, AppendRequest
from agent.core import build_agent, _checkpointer, _store
from agent.session import SessionManager
from agent.user_space import UserSpace
from agent.config import load_config
from skills.system import SYSTEM_SKILLS

router = APIRouter()
session_mgr = SessionManager()
cfg = load_config()


def _get_agent(user_id: str):
    us = UserSpace(user_id, cfg.persistence.data_dir)
    system_prompt = us.get_agents_md()
    return build_agent(
        user_id=user_id,
        system_prompt=system_prompt,
        skills=SYSTEM_SKILLS,
        interrupt_on=cfg.agent.interrupt_on,
        cfg=cfg,
    )


async def _stream_agent(user_id: str, message: str):
    agent = _get_agent(user_id)
    langgraph_config = session_mgr.get_config(user_id)
    input_msg = {"messages": [HumanMessage(content=message)]}

    try:
        async for event in agent.astream_events(input_msg, config=langgraph_config, version="v2"):
            kind = event["event"]

            if kind == "on_tool_start":
                data = {"type": "tool_start", "tool": event["name"], "msg": f"调用 {event['name']}..."}
                yield f"event: step\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

            elif kind == "on_tool_end":
                output = event.get("data", {}).get("output", {})
                if isinstance(output, dict) and output.get("type") in ["line", "bar", "pie", "scatter", "heatmap"]:
                    yield f"event: chart\ndata: {json.dumps(output, ensure_ascii=False)}\n\n"
                else:
                    data = {"type": "tool_end", "tool": event["name"], "msg": f"{event['name']} 执行完成"}
                    yield f"event: step\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

            elif kind == "on_chat_model_stream":
                chunk = event["data"].get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    data = {"token": chunk.content}
                    yield f"event: token\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    except Exception as e:
        # 检测 interrupt（LangGraph 在 interrupt_on 触发时抛出特定异常或通过 __interrupt__ 传递）
        error_str = str(e)
        if "interrupt" in error_str.lower() or "__interrupt__" in error_str:
            data = {
                "type": "confirm_required",
                "action": "operation",
                "msg": f"操作需要确认：{error_str[:200]}",
                "preview": []
            }
            yield f"event: interrupt\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        else:
            data = {"error": error_str}
            yield f"event: error\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    yield f"event: done\ndata: {json.dumps({'status': 'complete'}, ensure_ascii=False)}\n\n"


@router.post("/{user_id}")
async def chat(user_id: str, req: ChatRequest):
    return StreamingResponse(
        _stream_agent(user_id, req.message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{user_id}/confirm")
async def confirm(user_id: str, req: ConfirmRequest):
    agent = _get_agent(user_id)
    langgraph_config = session_mgr.get_config(user_id)
    resume_value = True if req.action == "approve" else False
    agent.invoke(Command(resume=resume_value), config=langgraph_config)
    return {"status": "ok", "action": req.action}


@router.post("/{user_id}/cancel")
async def cancel(user_id: str):
    return {"status": "cancelled"}


@router.post("/{user_id}/append")
async def append_message(user_id: str, req: AppendRequest):
    agent = _get_agent(user_id)
    langgraph_config = session_mgr.get_config(user_id)
    agent.invoke(Command(resume={"additional_context": req.message}), config=langgraph_config)
    return {"status": "appended", "message": req.message}
