from aiogram import BaseMiddleware
from aiogram.enums import ChatType


class PrivateAllowlistMiddleware(BaseMiddleware):
    def __init__(self, allowed_ids):
        self.allowed_ids = frozenset(allowed_ids)

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        chat = data.get("event_chat")
        if user is None or chat is None:
            return None
        if user.id not in self.allowed_ids or chat.type != ChatType.PRIVATE:
            return None
        return await handler(event, data)


def install_access_guard(dispatcher, allowed_ids):
    dispatcher.update.outer_middleware(PrivateAllowlistMiddleware(allowed_ids))
