from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None  # 不传时后端生成新 uuid，作为 thread_id 的一部分


class ConfirmRequest(BaseModel):
    action: str        # "approve" | "cancel"
    session_id: str


class AppendRequest(BaseModel):
    message: str
    conversation_id: str | None = None  # 必须和发起 chat 时一致才能命中正确 thread


class SkillCreateRequest(BaseModel):
    name: str
    description: str
    code: str
