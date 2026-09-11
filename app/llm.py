"""Hy3 (腾讯云 TokenHub) OpenAI 兼容调用层。

职责：
- 从 .env 读取 HY3_BASE_URL / HY3_API_KEY / HY3_MODEL；
- 封装官方 openai SDK，提供 chat() 与 chat_json()；
- 对限流(429)与瞬时错误做指数退避重试；
- chat_json() 在 JSON 解析失败时再做一次 schema 重试；
- 批量并发（混元默认 5）由调用方在 eval/run_eval 用信号量控制，本模块只保证单调用健壮。

运行自测：python -m app.llm
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# 混元默认并发上限为 5；批量评测时由 run_eval 用信号量约束
MAX_CONCURRENCY = 5
MAX_RETRIES = 2

_RETRYABLE_HINTS = ("rate", "429", "timeout", "timed out", "503", "502", "500",
                     "connection", "reset", "econn")


@dataclass
class Hy3Client:
    base_url: str = os.getenv("HY3_BASE_URL", "https://tokenhub.tencentmaas.com/v1")
    api_key: str = os.getenv("HY3_API_KEY", "")
    model: str = os.getenv("HY3_MODEL", "hy3")
    temperature: float = float(os.getenv("HY3_TEMPERATURE", "0.2"))
    top_p: float = float(os.getenv("HY3_TOP_P", "1.0"))
    timeout: int = int(os.getenv("HY3_TIMEOUT_SECONDS", "120"))
    send_sampling_params: bool = os.getenv("HY3_SEND_SAMPLING_PARAMS", "0") == "1"
    max_retries: int = MAX_RETRIES

    def __post_init__(self) -> None:
        if not self.api_key:
            raise RuntimeError(
                "HY3_API_KEY 未设置：请在本地 .env 填入腾讯云 TokenHub 的 API Key"
            )
        self._client = OpenAI(
            base_url=self.base_url, api_key=self.api_key, timeout=self.timeout
        )

    def chat(
        self,
        messages: Sequence[dict],
        *,
        json_mode: bool = False,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> str:
        """单次对话补全，返回 assistant 文本。"""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "stream": False,
        }
        # TokenHub 官方 curl 示例只包含 model/messages/stream；默认保持最小
        # payload，避免部分部署对额外采样参数兼容性不一致。
        if temperature is not None:
            payload["temperature"] = temperature
        elif self.send_sampling_params:
            payload["temperature"] = self.temperature
        if self.send_sampling_params:
            payload["top_p"] = self.top_p
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        payload.update(kwargs)

        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.chat.completions.create(**payload)
                return resp.choices[0].message.content or ""
            except Exception as err:  # noqa: BLE001 - SDK 抛出多种异常类型
                last_err = err
                if attempt < self.max_retries and self._is_retryable(err):
                    time.sleep(min(2**attempt, 8))
                    continue
                raise
        raise RuntimeError(f"Hy3 调用失败: {last_err}") from last_err

    def chat_json(
        self, messages: Sequence[dict], *, temperature: float | None = None
    ) -> dict:
        """请求 JSON 模式补全并解析；解析失败时重试一次（要求纯 JSON 输出）。"""
        raw = self.chat(messages, json_mode=True, temperature=temperature)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            repair = list(messages) + [
                {
                    "role": "user",
                    "content": "只输出合法 JSON，不要任何额外说明或 Markdown 代码块。",
                }
            ]
            return json.loads(self.chat(repair, json_mode=True, temperature=temperature))

    @staticmethod
    def _is_retryable(err: Exception) -> bool:
        return any(hint in str(err).lower() for hint in _RETRYABLE_HINTS)


def main() -> None:
    client = Hy3Client()
    reply = client.chat([{"role": "user", "content": "你好，用一句话介绍你自己。"}])
    print("Hy3 返回：", reply)


if __name__ == "__main__":
    main()
