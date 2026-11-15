"""Offline voice policy, synthesis and QQ upload regression tests."""

import io
import json
import unittest
import wave
from unittest.mock import AsyncMock, patch

import test_qq_adapter as adapter_tests
from test_qq_adapter import vf, group_event, private_event
from aiohttp import web
from nonebot.adapters.qq import Message
from nonebot.adapters.qq.exception import ActionFailed, NetworkError
from nonebot.drivers import Response
from plugins.Virtual_friends_qq.tts import (
    ReplyDecision,
    SpeechClient,
    VoiceDelivery,
    parse_reply,
    validate_voice_config,
)

SESSION = "qq:app-one:group:opaque-group"
DECISION = ReplyDecision("太好了！", True)


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_parse(self):
        base = dict(
            text="太好了！",
            delivery="voice",
        )
        self.assertTrue(parse_reply(json.dumps(base)).voice)
        for value in ("text", "invalid", None, True):
            self.assertFalse(parse_reply(json.dumps({**base, "delivery": value})).voice)
        self.assertTrue(
            parse_reply(
                json.dumps({**base, "intensity": 0, "voice_benefit": False})
            ).voice
        )
        self.assertEqual(parse_reply('{"text":"你好", broken').text, "你好")
        self.assertFalse(parse_reply('{"text":"你好", broken').voice)
        self.assertEqual(parse_reply("普通回复").text, "普通回复")
        self.assertFalse(parse_reply(r'{"text":"bad\q"}').voice)
        self.assertNotIn("delivery", parse_reply('{"delivery":').text)

    async def test_message_type_and_scenes(self):
        delivery = VoiceDelivery()
        delivery.client.synthesize = AsyncMock(return_value=b"wav")
        send = AsyncMock(return_value="sent")
        # Old thresholds, length preferences and cooldown settings must no longer gate voice.
        config = {
            "voice_enabled": True,
            "voice_threshold": 4,
            "voice_max_chars": 1,
            "voice_cooldown_seconds": 86400,
        }
        for _ in range(2):
            result = await delivery.send(SESSION, "字" * 121, DECISION, config, send)
            self.assertEqual(result, ("sent", True))
            self.assertEqual(send.call_args.args[0].type, "file_audio")
        for config, decision, session in [
            ({"voice_enabled": False}, DECISION, SESSION),
            ({}, ReplyDecision("hi"), SESSION),
            ({}, DECISION, "qq:app:channel:x"),
        ]:
            self.assertFalse(
                (await delivery.send(session, "hi", decision, config, send))[1]
            )
        self.assertEqual(delivery.client.synthesize.await_count, 2)

    async def test_switch_overrides_voice_and_test_command(self):
        delivery = VoiceDelivery()
        delivery.client.synthesize = AsyncMock(return_value=b"wav")
        send = AsyncMock()
        for config in ({}, {"voice_enabled": False}):
            for force in (False, True):
                result = await delivery.send(
                    SESSION, "正文", DECISION, config, send, force=force
                )
                self.assertFalse(result[1])
                self.assertIsInstance(send.call_args.args[0], str)
        delivery.client.synthesize.assert_not_awaited()
        result = await delivery.send(
            SESSION,
            "正文",
            ReplyDecision("正文"),
            {"voice_enabled": True},
            send,
            force=True,
        )
        self.assertTrue(result[1])

    async def test_fallback_and_unknown_send(self):
        delivery = VoiceDelivery()
        send = AsyncMock()
        delivery.client.synthesize = AsyncMock(side_effect=TimeoutError)
        await delivery.send(SESSION, "hello", DECISION, {"voice_enabled": True}, send)
        send.assert_awaited_once_with("hello")
        delivery.client.synthesize = AsyncMock(return_value=b"wav")
        send = AsyncMock(
            side_effect=[ActionFailed(Response(400, content=b"{}")), "fallback"]
        )
        self.assertFalse(
            (
                await delivery.send(
                    SESSION, "hello", DECISION, {"voice_enabled": True}, send
                )
            )[1]
        )
        self.assertEqual(send.await_count, 2)
        for error in (
            NetworkError("unknown"),
            ActionFailed(Response(503, content=b"{}")),
        ):
            send = AsyncMock(side_effect=error)
            with self.assertRaises(type(error)):
                await delivery.send(
                    SESSION, "hello", DECISION, {"voice_enabled": True}, send
                )
            self.assertEqual(send.await_count, 1)

    async def test_concurrent_voice_requests(self):
        import asyncio

        delivery = VoiceDelivery()
        delivery.client.synthesize = AsyncMock(return_value=b"wav")
        send = AsyncMock()
        results = await asyncio.gather(
            *[
                delivery.send(SESSION, "hi", DECISION, {"voice_enabled": True}, send)
                for _ in range(2)
            ]
        )
        self.assertTrue(all(result[1] for result in results))
        self.assertEqual(delivery.client.synthesize.await_count, 2)

    async def test_synthesis_real_websocket(self):
        body = b"\x00\x00" * 2400
        mode = "ok"
        received = []

        async def handler(request):
            self.assertEqual(request.headers["Authorization"], "Bearer test-key")
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            run = await ws.receive_json()
            received.append(run)
            task_id = run["header"]["task_id"]

            async def emit(kind):
                await ws.send_json({"header": {"event": kind, "task_id": task_id}})

            if mode == "failure":
                await emit("task-failed")
            elif mode == "mismatch":
                await ws.send_json(
                    {"header": {"event": "task-started", "task_id": "wrong"}}
                )
            else:
                await emit("task-started")
                for action in ("continue-task", "finish-task"):
                    message = await ws.receive_json()
                    self.assertEqual(message["header"]["action"], action)
                    self.assertEqual(message["header"]["task_id"], task_id)
                    if action == "continue-task":
                        self.assertEqual(
                            message["payload"]["input"]["text"], "指定文字"
                        )
                await ws.send_bytes(body)
                if mode != "disconnect":
                    await emit("task-finished")
            await ws.close()
            return ws

        app = web.Application()
        app.router.add_get("/tts", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        config = {
            "tts_api_key": "test-key",
            "tts_ws_url": f"ws://127.0.0.1:{port}/tts",
            "tts_voice_id": "qwen-audio-3.0-tts-plus-cloned-test",
        }
        try:
            with patch.object(
                vf.ConfigManager,
                "get_env",
                side_effect=lambda key, default="", **kwargs: config.get(key, default),
            ):
                for voice in ("", "stella", "tide", "custom-clone"):
                    audio = await SpeechClient().synthesize("指定文字", voice)
                    with wave.open(io.BytesIO(audio)) as wav:
                        self.assertEqual(wav.getframerate(), 24000)
                        self.assertEqual(wav.getnframes(), 2400)
                    payload = received[-1]["payload"]
                    self.assertEqual(payload["model"], "qwen-audio-3.0-tts-plus")
                    self.assertEqual(payload["parameters"]["format"], "pcm")
                    self.assertEqual(
                        payload["parameters"]["voice"],
                        "custom-clone"
                        if voice == "custom-clone"
                        else config["tts_voice_id"],
                    )
                for mode in ("failure", "disconnect", "mismatch"):
                    with self.assertRaises(RuntimeError):
                        await SpeechClient().synthesize("指定文字", "")
                mode = "ok"
                for body in (b"odd", b"", b"\x00" * (24000 * 2 * 120 + 2)):
                    with self.assertRaises(ValueError):
                        await SpeechClient().synthesize("指定文字", "")
                config["tts_voice_id"] = ""
                with self.assertRaises(ValueError):
                    await SpeechClient().synthesize("指定文字", "")
                config.pop("tts_api_key")
                config["chat_api_key"] = "must-not-use"
                with self.assertRaises(ValueError):
                    await SpeechClient().synthesize("指定文字", "custom-clone")
        finally:
            await runner.cleanup()

    async def test_test_command(self):
        with patch.object(vf.voice_delivery, "send", new_callable=AsyncMock) as send:
            await vf.handle_test_voice(
                type("Bot", (), {"self_id": "app-one"})(),
                group_event(),
                Message("指定朗读文字"),
            )
            self.assertEqual(send.call_args.args[1], "指定朗读文字")
            self.assertTrue(send.call_args.kwargs["force"])
        for config in (
            {"voice_enabled": "false"},
            {"voice_id": 123},
            {"voice_id": "invalid voice"},
        ):
            with self.assertRaises(ValueError):
                validate_voice_config(config)

    async def test_voice_prompt_reload(self):
        import tempfile
        from pathlib import Path

        config = vf.ConfigManager()
        with tempfile.TemporaryDirectory() as directory:
            config.base_path = Path(directory)
            config.personas_path = config.base_path / "personas.json"
            config.groups_config_path = config.base_path / "groups.json"
            await config.initialize()
            data = json.loads(config.personas_path.read_text())
            self.assertEqual(next(iter(data)), "_voice_prompt")
            self.assertNotIn("_voice_prompt", config.get_personas())
            self.assertFalse(config.add_persona("_voice_prompt", "bad"))
            self.assertFalse(config.delete_persona("_voice_prompt"))
            self.assertFalse(config.update_persona("_voice_prompt", "bad"))
            data["_voice_prompt"] = "自定义语音判断规则：返回 text 和 delivery"
            data = {
                key: value for key, value in data.items() if key != "_voice_prompt"
            } | {"_voice_prompt": data["_voice_prompt"]}
            config.personas_path.write_text(json.dumps(data))
            self.assertTrue(await config.reload())
            self.assertEqual(
                next(iter(json.loads(config.personas_path.read_text()))),
                "_voice_prompt",
            )
            with (
                patch.object(vf.chat_client, "config", config),
                patch.object(
                    vf.chat_client,
                    "generate_response",
                    AsyncMock(return_value='{"text":"hi"}'),
                ) as generate,
            ):
                await vf.chat_client.generate_chat_reply(
                    [{"role": "user", "content": "hello"}], voice_enabled=True
                )
                self.assertEqual(
                    generate.call_args.args[0][0]["content"], data["_voice_prompt"]
                )
            self.assertTrue(config.add_persona("new", "新的人设"))
            self.assertEqual(
                json.loads(config.personas_path.read_text())["_voice_prompt"],
                data["_voice_prompt"],
            )
            data["_voice_prompt"] = ""
            config.personas_path.write_text(json.dumps(data))
            self.assertFalse(await config.reload())
            self.assertTrue(config.get_voice_prompt())
            del data["_voice_prompt"]
            config.personas_path.write_text(json.dumps(data))
            self.assertTrue(await config.reload())
            self.assertEqual(
                next(iter(json.loads(config.personas_path.read_text()))),
                "_voice_prompt",
            )

    async def test_per_group_voice_config(self):
        import tempfile
        from pathlib import Path

        config = vf.ConfigManager()
        with tempfile.TemporaryDirectory() as directory:
            config.groups_config_path = Path(directory) / "groups.json"
            config.groups_config_path.write_text(
                json.dumps(
                    {
                        "group-a": {"voice_enabled": True},
                        "group-b": {"group_name": "B"},
                    }
                )
            )
            config._load_groups()
            saved = json.loads(config.groups_config_path.read_text())
            self.assertTrue(saved["group-a"]["voice_enabled"])
            self.assertFalse(saved["group-b"]["voice_enabled"])
            config.update_instance_config("group-b", {"voice_enabled": True})
            config.update_instance_config("group-a", {"voice_enabled": False})
            self.assertFalse(config.get_instance_config("group-a")["voice_enabled"])
            self.assertTrue(config.get_instance_config("group-b")["voice_enabled"])
            config._load_groups()
            self.assertFalse(config.get_instance_config("group-a")["voice_enabled"])
            self.assertTrue(config.get_instance_config("group-b")["voice_enabled"])

    async def test_model_single_call(self):
        client = vf.chat_client
        with patch.object(
            client, "generate_response", AsyncMock(return_value='{"text":"hi"}')
        ) as generate:
            await client.generate_chat_reply(
                [{"role": "user", "content": "hello"}], voice_enabled=True
            )
            self.assertEqual(generate.await_count, 1)
            self.assertIn("delivery", generate.call_args.args[0][0]["content"])
            self.assertTrue(generate.call_args.kwargs["json_mode"])
            generate.reset_mock()
            await client.generate_chat_reply(
                [{"role": "user", "content": "hello"}], voice_enabled=False
            )
            self.assertFalse(generate.call_args.kwargs["json_mode"])
            self.assertEqual(generate.call_args.args[0][0]["role"], "user")

    async def test_active_delivery(self):
        with (
            patch(
                "plugins.Virtual_friends_qq.active_behavior.send_scheduled",
                new_callable=AsyncMock,
                return_value="app-one",
            ) as send,
            patch.object(
                vf.voice_delivery.client,
                "synthesize",
                new_callable=AsyncMock,
                return_value=b"wav",
            ),
        ):
            result = await vf.active_behavior_manager._deliver_message(
                SESSION, "太好了！", DECISION, {"voice_enabled": True}
            )
            self.assertEqual(result, (True, "app-one"))
            self.assertEqual(send.call_args.args[1].type, "file_audio")

    async def test_structured_reply_memory(self):
        fixture = adapter_tests.QQAdapterTests()
        fixture.setUp()
        payload = json.dumps(
            dict(
                text="太好了！",
                delivery="voice",
            )
        )
        with (
            patch.object(vf.config_manager, "is_in_whitelist", return_value=True),
            patch.object(vf.Message, "create", new_callable=AsyncMock) as create,
            patch.object(
                vf.context_builder,
                "build_context",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch.object(
                vf.chat_client,
                "generate_chat_reply",
                new_callable=AsyncMock,
                return_value=(payload, None),
            ),
            patch.object(vf.voice_delivery, "send", new_callable=AsyncMock) as send,
            patch.object(
                vf.memory_scheduler, "check_and_process", new_callable=AsyncMock
            ),
        ):
            await vf.handle_message(fixture.bot, group_event())
            self.assertEqual(create.call_args.kwargs["content"], "太好了！")
            self.assertEqual(create.call_args.kwargs["api_content"], "太好了！")
            self.assertTrue(send.call_args.args[2].voice)

    async def test_qq_audio_upload(self):
        fixture = adapter_tests.QQAdapterTests()
        fixture.setUp()
        bot = fixture.bot
        from nonebot.adapters.qq.models import Media

        for event, upload_name, post_name in [
            (group_event(), "post_group_files", "post_group_messages"),
            (private_event(), "post_c2c_files", "post_c2c_messages"),
        ]:

            async def call_api(api, **kwargs):
                if api == upload_name:
                    self.assertEqual(kwargs["file_type"], 3)
                    self.assertEqual(kwargs["file_data"], b"wav")
                    return Media(file_uuid="id", file_info="info", ttl=60)
                if api == post_name:
                    self.assertEqual(kwargs["msg_id"], event.id)
                    self.assertEqual(kwargs["msg_type"], 7)
                    return {"id": "sent"}
                self.fail(api)

            with patch.object(bot, "call_api", side_effect=call_api):
                await bot.send(
                    event, vf.MessageSegment.file_audio(b"wav", file_name="speech.wav")
                )


if __name__ == "__main__":
    unittest.main()
