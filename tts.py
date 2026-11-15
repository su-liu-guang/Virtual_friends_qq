"""Single-turn delivery decisions and bounded Qwen speech synthesis."""

import asyncio
import io
import json
import re
import wave
from uuid import uuid4
from dataclasses import dataclass

import aiohttp
from nonebot import logger
from nonebot.adapters.qq import MessageSegment
from nonebot.adapters.qq.exception import ActionFailed

from .config import ConfigManager

VOICE_DEFAULTS = {
    "voice_enabled": False,
    "voice_id": "",
}


@dataclass(frozen=True)
class ReplyDecision:
    text: str
    voice: bool = False


def parse_reply(raw: str | None) -> ReplyDecision:
    text = (raw or "").strip()
    candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        data = json.loads(candidate)
    except (ValueError, TypeError):
        # Recover only the text field of broken structured output; never voice.
        if candidate.startswith("{"):
            match = re.search(r'"text"\s*:\s*("(?:[^"\\]|\\.)*")', candidate)
            if match:
                try:
                    return ReplyDecision(json.loads(match.group(1)))
                except ValueError:
                    pass
            return ReplyDecision("抱歉，刚才的回复生成异常，请再试一次。")
        return ReplyDecision(text)
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        return ReplyDecision("抱歉，刚才的回复生成异常，请再试一次。")
    return ReplyDecision(data["text"].strip(), data.get("delivery") == "voice")


def validate_voice_config(config: dict) -> None:
    for key, value in config.items():
        if key == "voice_enabled" and type(value) is not bool:
            raise ValueError("语音开关必须是 true 或 false")
        if key == "voice_id" and (
            not isinstance(value, str)
            or (value and not re.fullmatch(r"[A-Za-z0-9_.-]+", value))
        ):
            raise ValueError("语音音色须为音色 ID，留空时使用 TTS_VOICE_ID")


def supports_voice(session_id: str) -> bool:
    parts = session_id.split(":", 3)
    return len(parts) == 4 and parts[0] == "qq" and parts[2] in {"group", "c2c"}


class SpeechClient:
    async def synthesize(self, text: str, voice_id: str) -> bytes:
        if not text.strip() or len(text) > 500:
            raise ValueError("语音文字须为 1—500 字")
        # Retired built-in voices in existing group configs use the global Qwen voice.
        if voice_id in {"", "stella", "tide"}:
            voice_id = ConfigManager.get_env("tts_voice_id", "", warn_missing=False)
        validate_voice_config({"voice_id": voice_id})
        if not voice_id or voice_id in {"stella", "tide"}:
            raise ValueError("请配置 Qwen 复刻音色 TTS_VOICE_ID 或当前群的语音音色")
        key = ConfigManager.get_env("tts_api_key", "", warn_missing=False)
        if not key:
            raise ValueError("未配置百炼 TTS_API_KEY")
        url = ConfigManager.get_env(
            "tts_ws_url", "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
        )
        # wait_for bounds the entire WebSocket task, including stalled audio streams.
        pcm = await asyncio.wait_for(self._receive_audio(text, voice_id, key, url), 45)
        if not pcm or len(pcm) % 2:
            raise ValueError("TTS 音频为空或不完整")
        output = io.BytesIO()
        with wave.open(output, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes(bytes(pcm))
        return output.getvalue()

    async def _receive_audio(
        self, text: str, voice_id: str, key: str, url: str
    ) -> bytes:
        task_id = uuid4().hex

        def event(action, payload):
            return {
                "header": {"action": action, "task_id": task_id, "streaming": "duplex"},
                "payload": payload,
            }

        pcm = bytearray()
        started = False
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                url,
                headers={"Authorization": f"Bearer {key}"},
                max_msg_size=8 * 1024 * 1024,
            ) as ws:
                await ws.send_json(
                    event(
                        "run-task",
                        {
                            "task_group": "audio",
                            "task": "tts",
                            "function": "SpeechSynthesizer",
                            "model": "qwen-audio-3.0-tts-plus",
                            "parameters": {
                                "text_type": "PlainText",
                                "voice": voice_id,
                                "format": "pcm",
                                "sample_rate": 24000,
                            },
                            "input": {},
                        },
                    )
                )
                async for message in ws:
                    if message.type == aiohttp.WSMsgType.BINARY:
                        if not started:
                            raise RuntimeError("Qwen 在任务开始前返回音频")
                        pcm.extend(message.data)
                        if len(pcm) > 24_000 * 2 * 120:
                            raise ValueError("语音超过 120 秒限制")
                    elif message.type == aiohttp.WSMsgType.TEXT:
                        header = json.loads(message.data)["header"]
                        if header.get("task_id") != task_id:
                            raise RuntimeError("Qwen 返回的任务 ID 不匹配")
                        kind = header.get("event")
                        if kind == "task-failed":
                            raise RuntimeError("Qwen 语音合成任务失败")
                        if kind == "task-started":
                            if started:
                                raise RuntimeError("Qwen 重复启动任务")
                            started = True
                            await ws.send_json(
                                event("continue-task", {"input": {"text": text}})
                            )
                            await ws.send_json(event("finish-task", {"input": {}}))
                        elif kind == "task-finished":
                            if not started:
                                raise RuntimeError("Qwen 任务未启动即结束")
                            return bytes(pcm)
                    elif message.type == aiohttp.WSMsgType.ERROR:
                        raise RuntimeError("Qwen WebSocket 接收失败")
        raise RuntimeError("Qwen 连接提前关闭，音频不完整")


class VoiceDelivery:
    def __init__(self):
        self.client = SpeechClient()
        self.capacity = asyncio.Semaphore(3)

    async def send(
        self, session_id, text, decision, config, send, *, fallback=None, force=False
    ):
        """Return (send result, delivered as voice). Unknown send errors propagate."""
        cfg = {**VOICE_DEFAULTS, **config}
        validate_voice_config(cfg)
        fallback = text if fallback is None else fallback
        if not cfg["voice_enabled"]:
            return await send(
                "当前会话未启用语音，请先设置语音开关为 true。" if force else fallback
            ), False
        eligible = force or decision.voice
        if not supports_voice(session_id) or not eligible:
            return await send(fallback), False
        try:
            async with self.capacity:
                audio = await self.client.synthesize(text, cfg["voice_id"])
        except Exception as exc:
            logger.warning(f"[TTS] 合成失败，回退文字: {type(exc).__name__}")
            if force:
                return await send("语音合成失败，请检查 TTS 配置后重试。"), False
            return await send(fallback), False
        try:
            result = await send(
                MessageSegment.file_audio(audio, file_name="speech.wav")
            )
        except ActionFailed as exc:
            # 5xx may have accepted the message; do not duplicate it.
            if not 400 <= exc.status_code < 500:
                raise
            logger.warning(
                f"[TTS] QQ 拒绝语音: status={exc.status_code}, code={exc.code}"
            )
            return await send(
                "QQ 拒绝了语音，请检查机器人媒体权限或音频格式。" if force else fallback
            ), False
        return result, True


voice_delivery = VoiceDelivery()
