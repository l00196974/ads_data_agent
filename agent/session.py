class SessionManager:
    def get_thread_id(self, user_id: str) -> str:
        return user_id

    def get_config(self, user_id: str) -> dict:
        return {"configurable": {"thread_id": self.get_thread_id(user_id)}}
