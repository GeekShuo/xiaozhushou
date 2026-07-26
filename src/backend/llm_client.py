"""极简 OpenAI 兼容 LLM 客户端(纯标准库实现,无第三方依赖)。

支持任何兼容 ``/chat/completions`` 的服务:
    - OpenAI:        https://api.openai.com/v1
    - DeepSeek:      https://api.deepseek.com/v1
    - 通义千问:      https://dashscope.aliyuncs.com/compatible-mode/v1
    - 本地 Ollama:   http://localhost:11434/v1

配置优先级:环境变量 > config.json 的 llm 字段 > 内置默认值。
"""
from __future__ import annotations

import os
import json
import urllib.request
import urllib.error

# 环境变量名(便于在桌面端/容器里不落盘地注入密钥)
ENV_BASE_URL = "LIFECOACH_LLM_BASE_URL"
ENV_MODEL = "LIFECOACH_LLM_MODEL"
ENV_API_KEY = "LIFECOACH_LLM_API_KEY"
ENV_OPENAI_KEY = "OPENAI_API_KEY"  # 兼容通用写法

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TEMPERATURE = 0.7


def resolve_llm_config(config: dict | None):
    """从 (环境变量 + config.json) 解析出 (base_url, model, api_key, temperature)。"""
    config = config or {}
    llm = config.get("llm", {}) if isinstance(config, dict) else {}

    base_url = os.environ.get(ENV_BASE_URL) or llm.get("base_url") or DEFAULT_BASE_URL
    model = os.environ.get(ENV_MODEL) or llm.get("model") or DEFAULT_MODEL

    api_key = (
        os.environ.get(ENV_API_KEY)
        or os.environ.get(ENV_OPENAI_KEY)
        or (llm.get("api_key") if isinstance(llm, dict) else "")
        or ""
    )

    try:
        temperature = float(llm.get("temperature", DEFAULT_TEMPERATURE))
    except (TypeError, ValueError):
        temperature = DEFAULT_TEMPERATURE

    vision = bool(llm.get("vision", False))

    return base_url, model, api_key, temperature, vision


class LLMError(Exception):
    """LLM 调用相关的错误。"""


class LLMClient:
    """基于 urllib 的 OpenAI 兼容客户端。"""

    def __init__(self, base_url: str, api_key: str, model: str, temperature: float = DEFAULT_TEMPERATURE):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or ""
        self.model = model or DEFAULT_MODEL
        self.temperature = temperature

    def chat(self, messages: list[dict], json_mode: bool = False, max_tokens: int | None = None) -> str:
        """发起一次对话补全,返回助手消息文本。失败抛 LLMError。"""
        url = self.base_url + "/chat/completions"
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if max_tokens:
            payload["max_tokens"] = int(max_tokens)

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        if self.api_key:
            req.add_header("Authorization", "Bearer " + self.api_key)

        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "ignore")[:500]
            except Exception:
                pass
            raise LLMError(f"HTTP {e.code}: {detail}")
        except Exception as e:  # 网络错误等
            raise LLMError(f"请求失败: {e}")

        try:
            obj = json.loads(body)
            return obj["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise LLMError(f"响应解析失败: {e}")


def strip_json(content: str) -> dict:
    """把模型可能包裹了 ```json ... ``` 的代码块还原成 dict。失败返回 {}。"""
    if not content:
        return {}
    text = content.strip()
    if text.startswith("```"):
        # 去掉首行 ```json 与结尾 ```
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    # 退而求其次:截取第一个 { 到最后一个 }
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e != -1 and e > s:
        try:
            return json.loads(text[s:e + 1])
        except ValueError:
            return {}
    return {}
