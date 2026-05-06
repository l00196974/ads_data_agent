from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None  # 不传时后端生成新 uuid，作为 thread_id 的一部分


class ConfirmRequest(BaseModel):
    action: str        # "approve" | "cancel"
    session_id: str
    # 用户在弹窗里勾选了"以后不再确认 [工具名]"。runner 收到 True
    # 且当前工具是 medium_risk 时，写全局 auto_approve 白名单。
    # high_risk 即使 True 也会被忽略（runner 层硬挡）。
    add_to_auto_approve: bool = False


class AppendRequest(BaseModel):
    message: str
    conversation_id: str | None = None  # 必须和发起 chat 时一致才能命中正确 thread


class SkillCreateRequest(BaseModel):
    name: str
    description: str
    code: str


class RenameConversationRequest(BaseModel):
    title: str
