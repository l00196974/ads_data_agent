from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str


class ConfirmRequest(BaseModel):
    action: str  # "approve" | "cancel"


class AppendRequest(BaseModel):
    message: str


class SkillCreateRequest(BaseModel):
    name: str
    description: str
    code: str
