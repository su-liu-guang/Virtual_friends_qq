"""Offline regression tests using real QQ event/message models."""

import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
# Do not read the operator's credentials or create test data in the bot workspace.
_workspace = tempfile.TemporaryDirectory()
os.chdir(_workspace.name)
import nonebot
from nonebot.adapters.qq import Adapter, Bot, Message, MessageSegment
from nonebot.adapters.qq.config import BotInfo
from nonebot.adapters.qq.event import (
    C2CMessageCreateEvent,
    GroupAtMessageCreateEvent,
    GroupMessageCreateEvent,
    MessageCreateEvent,
    DirectMessageCreateEvent,
)

nonebot.init(driver="~fastapi+~httpx", log_level="ERROR", superusers={"qq:admin"})
plugin = nonebot.load_plugin("plugins.Virtual_friends_qq")
assert plugin is not None, "QQ plugin failed to load"
vf = plugin.module
from plugins.Virtual_friends_qq.qq_support import (
    conversation_id,
    is_public,
    nickname,
    send_scheduled,
)


def group_event(cls=GroupAtMessageCreateEvent, user="member", role="member", **kwargs):
    return cls.model_validate(
        dict(
            id="message-id",
            timestamp="2026-11-14T12:00:00+08:00",
            content="你好",
            group_id="legacy-group",
            group_openid="opaque-group",
            author=dict(id=user, member_openid=user, bot=False, member_role=role),
            **kwargs,
        )
    )


def private_event():
    return C2CMessageCreateEvent.model_validate(
        dict(
            id="private-id",
            timestamp="2026-11-14T12:00:00+08:00",
            content="你好",
            author=dict(id="opaque-group", user_openid="opaque-group"),
        )
    )


def guild_event(cls=MessageCreateEvent):
    return cls.model_validate(
        dict(
            id="guild-msg",
            channel_id="channel",
            guild_id="guild",
            content="你好",
            author=dict(id="member", username="昵称"),
        )
    )


class QQAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = Bot(
            Adapter(nonebot.get_driver()),
            "app-one",
            BotInfo(id="app-one", secret="test"),
        )

    async def test_enable_fetches_real_group_name_and_refreshes(self):
        from types import SimpleNamespace
        from nonebot.exception import FinishedException

        key = conversation_id(self.bot, group_event())
        with (
            patch.object(vf.config_manager, "groups", {}),
            patch.object(vf.config_manager, "_save_groups") as save,
            patch.object(Bot, "get_group_info", new_callable=AsyncMock) as fetch,
            patch.object(vf.enable_plugin, "finish", new_callable=AsyncMock,
                         side_effect=FinishedException) as finish,
        ):
            for event, name in [(group_event(), "真实群名"),
                                (group_event(GroupMessageCreateEvent), "改名后的群")]:
                fetch.return_value = SimpleNamespace(group_name=name)
                with self.assertRaises(FinishedException):
                    await vf.handle_enable_plugin(self.bot, event)
                fetch.assert_awaited_with(group_id="opaque-group")
                self.assertEqual(vf.config_manager.groups[key]["group_name"], name)
                self.assertTrue(vf.config_manager.groups[key]["whitelisted"])
                self.assertIn(name, finish.call_args.args[0])
            self.assertEqual(save.call_count, 2)

            for result in [RuntimeError("permission denied"),
                           asyncio.TimeoutError(), SimpleNamespace(group_name=" ")]:
                fetch.side_effect = result if isinstance(result, Exception) else None
                fetch.return_value = result
                with self.assertRaises(FinishedException):
                    await vf.handle_enable_plugin(self.bot, group_event())
                self.assertEqual(vf.config_manager.groups[key]["group_name"], "改名后的群")

    def test_real_models_and_session_isolation(self):
        events = [
            group_event(),
            private_event(),
            guild_event(),
            guild_event(DirectMessageCreateEvent),
        ]
        keys = [conversation_id(self.bot, event) for event in events]
        self.assertEqual(len(set(keys)), 4)
        self.assertEqual(keys[0], "qq:app-one:group:opaque-group")
        self.assertEqual(conversation_id(self.bot, group_event(user="other")), keys[0])
        other_bot = Bot(
            self.bot.adapter, "app-two", BotInfo(id="app-two", secret="test")
        )
        self.assertNotEqual(conversation_id(other_bot, events[0]), keys[0])
        self.assertEqual([is_public(e) for e in events], [True, False, True, False])
        self.assertEqual(nickname(events[2]), "昵称")
        self.assertEqual(nickname(events[0]), "用户member")

    async def test_passive_reply_ids_and_sequences(self):
        for event, method in [
            (group_event(), "send_to_group"),
            (private_event(), "send_to_c2c"),
            (guild_event(), "send_to_channel"),
            (guild_event(DirectMessageCreateEvent), "send_to_dms"),
        ]:
            with patch.object(Bot, method, new_callable=AsyncMock) as send:
                await self.bot.send(event, "生成中")
                await self.bot.send(event, MessageSegment.file_image(b"image"))
                self.assertEqual(send.await_count, 2)
                self.assertEqual(send.call_args.kwargs["msg_id"], event.id)
                if "msg_seq" in send.call_args.kwargs:
                    self.assertEqual(send.call_args_list[0].kwargs["msg_seq"], 1)
                    self.assertEqual(send.call_args_list[1].kwargs["msg_seq"], 2)

    async def test_scheduled_routing_and_failure(self):
        with (
            patch("nonebot.get_bot", return_value=self.bot) as get_bot,
            patch.object(Bot, "send_to_group", new_callable=AsyncMock) as send,
        ):
            await send_scheduled("qq:app-one:group:opaque-group", "hello")
            get_bot.assert_called_once_with("app-one")
            self.assertEqual(send.call_args.kwargs["group_openid"], "opaque-group")
            self.assertNotIn("msg_id", send.call_args.kwargs)
            send.side_effect = RuntimeError("platform denies active send")
            with self.assertRaises(RuntimeError):
                await send_scheduled("qq:app-one:group:opaque-group", "hello")
        with (
            patch("nonebot.get_bot", return_value=self.bot),
            patch.object(Bot, "send_to_channel", new_callable=AsyncMock) as send,
        ):
            await send_scheduled("qq:app-one:channel:channel", "hello")
            self.assertEqual(send.call_args.kwargs["channel_id"], "channel")
        for key in ["123456", "qq:app-one:c2c:member", "qq:app-one:dms:g/member"]:
            with self.assertRaises(ValueError):
                await send_scheduled(key, "hello")

    async def test_original_permission_categories(self):
        public = (vf.help_cmd, vf.memory_status, vf.view_persona, vf.identity_cmd)
        managers = (
            vf.switch_persona,
            vf.clear_memory,
            vf.persona_list,
            vf.add_persona,
            vf.enable_plugin,
            vf.disable_plugin,
            vf.view_config,
            vf.update_config,
            vf.reload_config,
            vf.test_voice_cmd,
        )
        superusers = (
            vf.delete_persona,
            vf.view_whitelist,
            vf.daily_summary_cmd,
            vf.yesterday_summary_cmd,
            vf.test_summary_cmd,
        )
        for role in ("member", "admin", "owner"):
            event = group_event(user="ordinary-user", role=role)
            for matcher in public:
                self.assertTrue(await matcher.permission(self.bot, event))
            for matcher in managers:
                self.assertEqual(
                    await matcher.permission(self.bot, event), role != "member"
                )
            for matcher in superusers:
                self.assertFalse(await matcher.permission(self.bot, event))
        for matcher in managers + superusers:
            self.assertTrue(
                await matcher.permission(self.bot, group_event(user="admin"))
            )
            self.assertFalse(await matcher.permission(self.bot, private_event()))
            self.assertFalse(await matcher.permission(self.bot, guild_event()))

    def test_qq_plugin_suffix(self):
        self.assertEqual(plugin.name, "Virtual_friends_qq")
        self.assertEqual(plugin.metadata.name, "Virtual Friends_qq")

    async def test_whitelist_blocks_model_and_database(self):
        with (
            patch.object(vf.config_manager, "is_in_whitelist", return_value=False),
            patch.object(vf.Message, "create", new_callable=AsyncMock) as create,
            patch.object(
                vf.chat_client, "generate_chat_reply", new_callable=AsyncMock
            ) as generate,
        ):
            await vf.handle_message(self.bot, group_event())
            create.assert_not_awaited()
            generate.assert_not_awaited()

    async def test_group_and_private_chat(self):
        for event in (group_event(), private_event()):
            with (
                patch.object(vf.config_manager, "is_in_whitelist", return_value=True),
                patch.object(vf.Message, "create", new_callable=AsyncMock) as create,
                patch.object(
                    vf.context_builder,
                    "build_context",
                    new_callable=AsyncMock,
                    return_value=[],
                ) as context,
                patch.object(
                    vf.chat_client,
                    "generate_chat_reply",
                    new_callable=AsyncMock,
                    return_value=("你好朋友", None),
                ),
                patch.object(
                    vf.message_handler, "send", new_callable=AsyncMock
                ) as send,
                patch.object(
                    vf.memory_scheduler, "check_and_process", new_callable=AsyncMock
                ),
            ):
                await vf.handle_message(self.bot, event)
                await asyncio.sleep(0)
                self.assertEqual(create.await_count, 2)
                self.assertEqual(
                    create.call_args_list[0].kwargs["user_id"], event.get_user_id()
                )
                self.assertEqual(
                    context.call_args.kwargs["group_id"],
                    conversation_id(self.bot, event),
                )
                self.assertIsInstance(send.call_args.args[0], Message)
                self.assertEqual(
                    send.call_args.args[0].extract_plain_text(), "你好朋友"
                )

    async def test_disabled_daily_summary_never_generates(self):
        with (
            patch.object(
                vf.config_manager,
                "groups",
                {"qq:app-one:group:g": {"summary_enabled": True, "whitelisted": False}},
            ),
            patch.object(
                vf.daily_summary_generator, "generate_report", new_callable=AsyncMock
            ) as generate,
        ):
            await vf.check_daily_summary()
            generate.assert_not_awaited()

    async def test_database_accepts_scoped_session_ids(self):
        from tortoise import Tortoise
        from plugins.Virtual_friends_qq.database import (
            init_db,
            Message as StoredMessage,
        )

        await init_db()
        await init_db()  # Repeated startup must keep the schema usable.
        try:
            key = "qq:" + "a" * 32 + ":group:" + "b" * 32
            row = await StoredMessage.create(
                group_id=key,
                user_id="member-openid",
                role="user",
                content="hello",
                timestamp=datetime.now(timezone.utc),
            )
            saved = await StoredMessage.get(id=row.id)
            self.assertEqual(saved.group_id, key)
        finally:
            await Tortoise.close_connections()

    def test_report_renderer_available(self):
        from plugins.Virtual_friends_qq.summary import html_to_pic

        self.assertTrue(
            callable(html_to_pic), "Compatible htmlrender API must be installed"
        )

    def test_local_image_uses_bytes(self):
        segment = MessageSegment.file_image(b"png")
        self.assertEqual(segment.type, "file_image")
        self.assertEqual(Bot._extract_qq_media(Message(segment))["file_data"], b"png")


if __name__ == "__main__":
    unittest.main()
