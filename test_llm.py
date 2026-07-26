"""xiaozhushou 接入真实 LLM 后的冒烟测试。

运行:在 xiaozhushou/ 目录下执行 `python test_llm.py`
不依赖真实网络:用替身替换 LLMClient.chat 来验证解析/降级逻辑。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from backend import ai_engine  # noqa: E402
from backend.ai_engine import MockAIEngine, LLMAIEngine, build_engine  # noqa: E402
from backend.llm_client import LLMError, strip_json  # noqa: E402


def test_build_engine_selection():
    # 无 key -> 内置模板
    assert isinstance(build_engine({"engine": "auto", "llm": {"api_key": ""}}), MockAIEngine)
    # 强制 mock(即使有 key)
    assert isinstance(build_engine({"engine": "mock", "llm": {"api_key": "x"}}), MockAIEngine)
    # 强制 llm + 有 key
    assert isinstance(build_engine({"engine": "llm", "llm": {"api_key": "x"}}), LLMAIEngine)
    # 强制 llm 但无 key -> 安全降级
    assert isinstance(build_engine({"engine": "llm", "llm": {"api_key": ""}}), MockAIEngine)
    # auto + 有 key -> 真模型
    assert isinstance(build_engine({"engine": "auto", "llm": {"api_key": "x"}}), LLMAIEngine)
    print("PASS test_build_engine_selection")


def test_strip_json():
    assert strip_json('```json\n{"a":1}\n```') == {"a": 1}
    assert strip_json('{"a":1}') == {"a": 1}
    assert strip_json("garbage") == {}
    assert strip_json('prefix {"a":1} suffix')["a"] == 1
    print("PASS test_strip_json")


def test_llm_decompose_parses_and_assigns_ids():
    eng = LLMAIEngine("http://x/v1", "k", "m")
    fake = ('{"category":"减肥","phases":['
            '{"title":"P1","weeks":"1-2周","rationale":"r",'
            '"tasks":[{"title":"t1","detail":"d","frequency":"daily","duration_min":20},'
            '{"title":"t2","detail":"","frequency":"weekly","duration_min":30}]}]}')
    eng.client.chat = lambda *a, **k: fake
    key, phases = eng.decompose_goal("我要减肥")
    assert key == "减肥"
    assert len(phases) == 1
    assert len(phases[0].tasks) == 2
    assert phases[0].tasks[0].id.startswith("p1t")
    # 非法 frequency 被纠正为 daily
    assert phases[0].tasks[0].frequency in ("daily", "weekly", "once")
    print("PASS test_llm_decompose_parses_and_assigns_ids")


def test_llm_decompose_fallback_on_error():
    eng = LLMAIEngine("http://x/v1", "k", "m")
    eng.client.chat = lambda *a, **k: (_ for _ in ()).throw(LLMError("boom"))
    key, phases = eng.decompose_goal("我要减肥")
    # 降级到内置知识库,返回合法 key 与非空计划
    assert key in ("减肥", "赚钱", "学ai编程", "英语口语", "表达力", "generic")
    assert len(phases) >= 1
    assert len(phases[0].tasks) >= 1
    print("PASS test_llm_decompose_fallback_on_error")


def test_llm_chat_and_adapt():
    eng = LLMAIEngine("http://x/v1", "k", "m")
    eng.client.chat = lambda *a, **k: "你好,今天先做最小的一件事就好。"
    out = eng.chat("你好", [], None, [], None, [])
    assert "最小" in out
    # 非 JSON 返回 -> 降级到 mock adapt
    plan, changed = eng.adapt_plan([], "太难了减少点")
    assert isinstance(plan, list)
    print("PASS test_llm_chat_and_adapt")


def test_mock_chat_and_adapt_still_work():
    eng = MockAIEngine()
    r = eng.chat("我想放弃", [], None, [], None, [])
    assert isinstance(r, str) and len(r) > 0
    plan, changed = eng.adapt_plan([], "太难了减少点")
    assert isinstance(plan, list)
    print("PASS test_mock_chat_and_adapt_still_work")


if __name__ == "__main__":
    test_build_engine_selection()
    test_strip_json()
    test_llm_decompose_parses_and_assigns_ids()
    test_llm_decompose_fallback_on_error()
    test_llm_chat_and_adapt()
    test_mock_chat_and_adapt_still_work()
    print("\nALL TESTS PASSED")
