class SessionManager:
    def get_thread_id(self, user_id: str, conversation_id: str | None = None) -> str:
        """Build a langgraph thread_id.

        - With `conversation_id`: `"{user_id}_{conversation_id}"` — isolates
          per-conversation history so a single user's clicks of "新对话"
          don't all pile into the same growing thread.
        - Without: falls back to bare `user_id` (legacy compatibility).
        """
        if conversation_id:
            return f"{user_id}_{conversation_id}"
        return user_id

    def get_config(self, user_id: str, conversation_id: str | None = None) -> dict:
        return {
            "configurable": {"thread_id": self.get_thread_id(user_id, conversation_id)},
            "recursion_limit": 100,
        }
