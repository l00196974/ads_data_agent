"""Session helpers for agent.loop / ThreadStore（去 framework 后简化）。

历史 get_config() 是给 langgraph thread/recursion_limit 用的——P4b 删除框架
后不再需要那种 dict。保留 get_thread_id() 给 ThreadStore + middleware 寻址。
"""


class SessionManager:
    def get_thread_id(self, user_id: str, conversation_id: str | None = None) -> str:
        """Build thread_id for ThreadStore + middleware addressing.

        - With `conversation_id`: `"{user_id}_{conversation_id}"` — isolates
          per-conversation history so a single user's clicks of "新对话"
          don't all pile into the same growing thread.
        - Without: falls back to bare `user_id` (legacy compatibility).
        """
        if conversation_id:
            return f"{user_id}_{conversation_id}"
        return user_id
