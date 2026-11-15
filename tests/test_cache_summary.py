import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


ROOT_PATH = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT_PATH))
_workspace = tempfile.TemporaryDirectory()
os.chdir(_workspace.name)
os.environ["ENVIRONMENT"] = "cache_summary_test"

import nonebot

nonebot.init(log_level="WARNING")

from plugins.Virtual_friends_qq import cache_metrics
from plugins.Virtual_friends_qq.cache_metrics import AICallMetadata
from plugins.Virtual_friends_qq.clients import ChatClient
from plugins.Virtual_friends_qq import archive_now


class FakeCursor:
    def __init__(self, *, one=None, rows=None, rowcount=-1):
        self.one = one
        self.rows = rows or []
        self.rowcount = rowcount

    async def fetchone(self):
        return self.one

    async def fetchall(self):
        return self.rows


class ConflictDatabase:
    def __init__(self):
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def execute(self, sql, parameters=None):
        normalized = " ".join(sql.split()).lower()
        if normalized.startswith("select count"):
            return FakeCursor(one=(20,))
        if normalized.startswith("select id"):
            return FakeCursor(
                rows=[(index, f"摘要{index}", "2026.01.01-01.02") for index in range(20)]
            )
        if normalized.startswith("update summaries"):
            return FakeCursor(rowcount=19)
        return FakeCursor()


class CacheMetricsTests(unittest.TestCase):
    def test_usage_fields_are_serialized_without_request_content(self):
        usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=12,
            total_tokens=112,
            prompt_cache_hit_tokens=80,
            prompt_cache_miss_tokens=20,
        )
        bound_logger = Mock()
        fake_logger = Mock()
        fake_logger.bind.return_value = bound_logger
        messages = [
            {"role": "system", "content": "private-system-prompt"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "private-user-prompt"},
                    {"type": "file", "file_id": "private-file-id"},
                ],
            },
        ]

        with patch.object(cache_metrics, "logger", fake_logger), patch.object(
            cache_metrics, "_ensure_metric_level"
        ):
            cache_metrics.record_cache_usage(
                model="test-model",
                messages=messages,
                usage=usage,
                metadata=AICallMetadata(group_id="group", call_type="chat"),
            )

        payload = json.loads(bound_logger.log.call_args.args[1])
        self.assertEqual(payload["cache_hit_tokens"], 80)
        self.assertEqual(payload["cache_miss_tokens"], 20)
        self.assertEqual(payload["cache_hit_rate"], 0.8)
        self.assertEqual(payload["image_count"], 1)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("private-system-prompt", serialized)
        self.assertNotIn("private-user-prompt", serialized)
        self.assertNotIn("private-file-id", serialized)

    def test_missing_or_invalid_cache_fields_stay_null(self):
        usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=10,
            total_tokens=110,
            prompt_cache_hit_tokens="80",
            model_extra={"prompt_cache_miss_tokens": "20"},
        )
        bound_logger = Mock()
        fake_logger = Mock()
        fake_logger.bind.return_value = bound_logger

        with patch.object(cache_metrics, "logger", fake_logger), patch.object(
            cache_metrics, "_ensure_metric_level"
        ):
            cache_metrics.record_cache_usage(
                model="test-model",
                messages=[{"role": "user", "content": "hello"}],
                usage=usage,
                metadata=None,
            )

        payload = json.loads(bound_logger.log.call_args.args[1])
        self.assertIsNone(payload["cache_hit_tokens"])
        self.assertIsNone(payload["cache_miss_tokens"])
        self.assertIsNone(payload["cache_hit_rate"])


class ThinkingConfigTests(unittest.IsolatedAsyncioTestCase):
    def make_client(self, effort=None, enabled=None):
        values = {"chat_api_key": "test", "chat_api_url": "https://example.com"}
        if effort is not None:
            values["chat_reasoning_effort"] = effort
        if enabled is not None:
            values["chat_thinking_enabled"] = enabled
        config = Mock()
        config.get_env.side_effect = lambda key, default="": values.get(key, default)
        with patch("plugins.Virtual_friends_qq.clients.AsyncOpenAI"):
            client = ChatClient(config)
        client.client.chat.completions.create = AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="reply"))], usage=None,
        ))
        return client

    async def test_default_chat_disables_thinking(self):
        client = self.make_client()
        await client.generate_chat_reply([{"role": "user", "content": "hello"}])
        kwargs = client.client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["extra_body"]["thinking"]["type"], "disabled")
        self.assertNotIn("reasoning_effort", kwargs)
        self.assertIn("temperature", kwargs)

    async def test_levels_and_explicit_disable(self):
        for effort in ("low", "high", "max"):
            with self.subTest(effort=effort):
                client = self.make_client(effort, "true")
                await client.generate_chat_reply([{"role": "user", "content": "hello"}])
                kwargs = client.client.chat.completions.create.call_args.kwargs
                self.assertEqual(kwargs["reasoning_effort"], effort)
                self.assertEqual(kwargs["extra_body"]["thinking"]["type"], "enabled")
                self.assertNotIn("temperature", kwargs)
                await client.generate_response([], thinking_mode="disabled")
                kwargs = client.client.chat.completions.create.call_args.kwargs
                self.assertEqual(kwargs["extra_body"]["thinking"]["type"], "disabled")
                self.assertNotIn("reasoning_effort", kwargs)

    async def test_level_does_not_enable_thinking(self):
        client = self.make_client("max", "false")
        await client.generate_chat_reply([])
        kwargs = client.client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["extra_body"]["thinking"]["type"], "disabled")
        self.assertNotIn("reasoning_effort", kwargs)
        self.assertEqual(client.reasoning_effort, "max")

    def test_invalid_switch_rejected(self):
        with self.assertRaisesRegex(ValueError, "CHAT_THINKING_ENABLED"):
            self.make_client(enabled="typo")

    def test_invalid_level_rejected(self):
        with self.assertRaisesRegex(ValueError, "CHAT_REASONING_EFFORT"):
            self.make_client("typo")


class SummaryRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_l1_accepts_non_numbered_summary(self):
        client = ChatClient.__new__(ChatClient)
        summary = "大家讨论了周末安排，并确定星期六下午集合。"
        client.generate_response = AsyncMock(return_value=summary)

        result = await client.generate_summary("原始对话", retry=6, group_id="g")

        self.assertEqual(result, summary)
        self.assertEqual(client.generate_response.await_count, 1)

    async def test_l1_succeeds_on_sixth_attempt(self):
        client = ChatClient.__new__(ChatClient)
        valid = "1. " + "重要事项" * 500
        client.generate_response = AsyncMock(
            side_effect=["x" * 1000] * 4 + ["x" * 1501, valid]
        )

        result = await client.generate_summary("原始对话", retry=6, group_id="g")

        self.assertEqual(result, valid)
        self.assertEqual(client.generate_response.await_count, 6)
        for call in client.generate_response.await_args_list:
            self.assertEqual(call.kwargs["retry"], 1)
        self.assertIsNone(client.generate_response.await_args_list[5].kwargs["max_tokens"])

    async def test_l1_six_empty_attempts_leave_no_result(self):
        client = ChatClient.__new__(ChatClient)
        client.generate_response = AsyncMock(return_value=" ")

        result = await client.generate_summary("原始对话", retry=6, group_id="g")

        self.assertIsNone(result)
        self.assertEqual(client.generate_response.await_count, 6)

    async def test_archive_summary_retries_from_original_source(self):
        client = ChatClient.__new__(ChatClient)
        valid = "长期记忆" * 500
        client.generate_response = AsyncMock(
            side_effect=["x" * 1000] * 4 + ["x" * 1501, valid]
        )

        result = await client.generate_archive_summary(
            "原始L1内容",
            group_id="g",
            from_level=1,
            to_level=2,
        )

        self.assertEqual(result, valid)
        self.assertEqual(client.generate_response.await_count, 6)
        for call in client.generate_response.await_args_list:
            user_content = call.args[0][1]["content"]
            self.assertIn("原始L1内容", user_content)
        self.assertIsNone(client.generate_response.await_args_list[5].kwargs["max_tokens"])

    async def test_archive_summary_accepts_1500_chars_on_fifth_attempt(self):
        client = ChatClient.__new__(ChatClient)
        relaxed = "x" * 1500
        client.generate_response = AsyncMock(
            side_effect=["x" * 1000] * 4 + [relaxed]
        )

        result = await client.generate_archive_summary(
            "原始L1内容",
            group_id="g",
            from_level=1,
            to_level=2,
        )

        self.assertEqual(result, relaxed)
        self.assertEqual(client.generate_response.await_count, 5)

    async def test_manual_archive_sixth_attempt_has_no_length_limit(self):
        def response(text):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                usage=SimpleNamespace(prompt_tokens=1),
            )

        create = AsyncMock(
            side_effect=[response("x" * 1000)] * 4
            + [response("x" * 1501), response("x" * 3000)]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        result = await archive_now.generate_bounded_summary(
            client,
            "test-model",
            "原始摘要",
            1,
            2,
        )

        self.assertEqual(result, "x" * 3000)
        self.assertEqual(create.await_count, 6)
        self.assertNotIn("max_tokens", create.await_args_list[5].kwargs)

    async def test_manual_archive_rolls_back_on_concurrent_update(self):
        database = ConflictDatabase()
        with patch.object(
            archive_now,
            "generate_bounded_summary",
            new=AsyncMock(return_value="合格摘要"),
        ):
            with self.assertRaises(RuntimeError):
                await archive_now.archive_level(
                    database,
                    Mock(),
                    "model",
                    "group",
                    1,
                    2,
                    20,
                )

        database.rollback.assert_awaited_once()
        database.commit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
