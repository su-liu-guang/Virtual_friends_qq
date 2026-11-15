"""QQ official adapter identity and outbound routing.

OpenIDs are opaque and application-scoped; never convert them to QQ numbers.
Interactive replies use matcher.send/finish so the adapter supplies msg_id/seq.
"""

import nonebot
from nonebot.adapters.qq import Bot, Message, MessageEvent, MessageSegment
from nonebot.adapters.qq.event import (
    C2CMessageCreateEvent,
    DirectMessageCreateEvent,
    GroupMessageCreateEvent,
    GuildMessageEvent,
)


def is_public(event: MessageEvent) -> bool:
    return isinstance(
        event, (GroupMessageCreateEvent, GuildMessageEvent)
    ) and not isinstance(event, DirectMessageCreateEvent)


def conversation_id(bot: Bot, event: MessageEvent) -> str:
    if isinstance(event, GroupMessageCreateEvent):
        kind, target = "group", event.group_openid
    elif isinstance(event, C2CMessageCreateEvent):
        kind, target = "c2c", event.get_user_id()
    elif isinstance(event, DirectMessageCreateEvent):
        kind, target = "dms", f"{event.guild_id}/{event.get_user_id()}"
    elif isinstance(event, GuildMessageEvent):
        kind, target = "channel", event.channel_id
    else:
        raise ValueError(f"Unsupported QQ message event: {type(event).__name__}")
    return f"qq:{bot.self_id}:{kind}:{target}"


def nickname(event: MessageEvent) -> str:
    member = getattr(event, "member", None)
    author = getattr(event, "author", None)
    return (
        getattr(member, "nick", None)
        or getattr(author, "username", None)
        or f"用户{event.get_user_id()}"
    )[:64]


async def send_scheduled(
    session_id: str, message: str | Message | MessageSegment
) -> str:
    """Send only through the owning application; platform errors propagate.

    No expired inbound message ID is reused to disguise an active send as a reply.
    Private conversations and legacy OneBot IDs are deliberately not scheduled.
    """
    parts = session_id.split(":", 3)
    if len(parts) != 4 or parts[0] != "qq" or parts[2] not in {"group", "channel"}:
        raise ValueError("定时发送需要 QQ 群聊或频道的完整会话标识")
    _, bot_id, kind, target = parts
    bot = nonebot.get_bot(bot_id)
    if not isinstance(bot, Bot):
        raise TypeError("会话所属 Bot 不是 QQ 官方适配器")
    if isinstance(message, str):
        message = Message(MessageSegment.text(message))
    if kind == "group":
        await bot.send_to_group(group_openid=target, message=message)
    else:
        await bot.send_to_channel(channel_id=target, message=message)
    return str(bot.self_id)
