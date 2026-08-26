"""调用本地 LLM 抽取单章结构化信息。

兼容 OpenAI Chat Completions 协议（POST /v1/chat/completions），
适用于：LM Studio、Ollama (OpenAI 兼容模式)、vLLM、text-generation-webui 等。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

from app.core.config import settings
from app.core.logger import logger

SYSTEM_PROMPT = """你是中文网络小说结构化分析助手。

【绝对规则】
1. 你的回复必须且只能是一个 JSON 对象；不允许任何额外文字、解释、注释、Markdown 标记。
2. 不要复述下面的字段定义，不要输出任何 prompt 片段。
3. JSON 必须能被标准 JSON 解析器（json.loads）正确解析。
4. 所有字符串值使用英文双引号；不要使用中文引号。
5. 即使章节内容看不懂也要按字段返回结构化数据，对应字段填 [] 或 ""。

【输出 schema】
{
  "new_character": [{"name": "人名", "description": "≤30字描述"}],
  "new_scene":     [{"name": "地点名", "description": "≤20字描述"}],
  "new_event":     [{"summary": "事件描述", "characters": ["人名"], "location": "地点", "importance": 1}],
  "summary":       "≤80字一句话摘要"
}

【抽取规则】
1. 只抽取本章首次出现的人物/地点；已在前章登场过的不要重复。
2. importance 取 1-5 整数，5 表示本章核心事件。
3. 若本章无新人物/新地点，对应数组返回 []。
4. 仅输出 JSON，第一字符必须是 {，最后一字符必须是 }。"""


def build_user_prompt(chapter: dict[str, Any]) -> str:
    title = chapter.get("title", "")
    content = chapter.get("content", "")
    if len(content) > settings.extract_max_chars:
        content = content[: settings.extract_max_chars] + "\n...(已截断)..."
    return f"章节号：{chapter.get('chapter_id')}\n章节标题：{title}\n\n章节正文：\n{content}"


def _call_llm(prompt: str) -> str:
    """OpenAI 兼容协议：POST {base_url}/v1/chat/completions"""
    base = settings.llm_base_url.rstrip("/")
    url = f"{base}/v1/chat/completions"
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "stream": False,
    }
    resp = requests.post(url, json=payload, timeout=settings.llm_timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f"LLM HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()

    # OpenAI 风格错误体
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"LLM 返回错误: {data['error']}")

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(
            f"LLM 响应缺少 choices[0].message.content: "
            f"{json.dumps(data, ensure_ascii=False)[:500]}"
        ) from e

    if not content or not content.strip():
        raise RuntimeError(
            f"LLM 返回空内容。原始响应: {json.dumps(data, ensure_ascii=False)[:500]}"
        )
    return content


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_balanced_json(text: str) -> str | None:
    """在文本中扫描，返回最长的一段花括号平衡的 JSON 对象子串。

    只接受以 `{` 开头且紧跟 `"` 的片段（合法 JSON 对象起手），
    避免抓到 prompt 里 `{summary: str, ...}` 这种字段说明文本。
    """
    best: str | None = None
    depth = 0
    start_idx = -1
    in_str = False
    escape = False
    for i in range(len(text)):
        c = text[i]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            if depth == 0:
                # 合法 JSON 对象起手：{" 或 {空白+"
                nxt = text[i + 1] if i + 1 < len(text) else ""
                if nxt not in ('"', " ", "\t", "\n", "\r"):
                    # 不是 JSON 对象，跳过
                    pass
                else:
                    start_idx = i
                    depth = 1
        elif c == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start_idx >= 0:
                    candidate = text[start_idx : i + 1]
                    if best is None or len(candidate) > len(best):
                        best = candidate
                    start_idx = -1
    return best


def _strip_to_json(text: str) -> str:
    """把模型输出收敛成可解析的 JSON 字符串。

    兼容：
    1. 整段就是 JSON；
    2. ```json ... ``` 包裹；
    3. JSON 之前/之后夹杂解释文字（qwen3 reasoning tokens）。
    """
    text = text.strip()
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    j = _extract_balanced_json(text)
    if j:
        return j
    # 兜底：粗暴截取
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def _find_all_balanced_objects(text: str) -> list[str]:
    """找出文本中所有花括号平衡的"看起来像 JSON 对象"的子串。"""
    results: list[str] = []
    depth = 0
    start_idx = -1
    in_str = False
    escape = False
    for i in range(len(text)):
        c = text[i]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            if depth == 0:
                nxt = text[i + 1] if i + 1 < len(text) else ""
                if nxt in ('"', " ", "\t", "\n", "\r"):
                    start_idx = i
                    depth = 1
        elif c == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start_idx >= 0:
                    results.append(text[start_idx : i + 1])
                    start_idx = -1
    return results


def _try_parse_any_object(text: str) -> dict | None:
    """依次尝试解析文本中每一个平衡 JSON 对象，返回第一个成功且
    包含 new_character/new_scene/new_event/summary 至少一项的对象。
    """
    for cand in _find_all_balanced_objects(text):
        try:
            obj = json.loads(cand)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        if any(k in obj for k in ("new_character", "new_scene", "new_event", "summary")):
            return obj
    return None


def _empty_result() -> dict[str, Any]:
    return {
        "new_character": [],
        "new_scene": [],
        "new_event": [],
        "summary": "",
    }


def extract_chapter(chapter: dict[str, Any]) -> dict[str, Any]:
    """对单章调用 LLM，返回结构化结果（失败时返回空结果，并打日志）。"""
    prompt = build_user_prompt(chapter)
    last_err: Exception | None = None
    for attempt in range(1, settings.llm_max_retries + 1):
        t0 = time.time()
        try:
            raw = _call_llm(prompt)
            # 1) 先尝试直接解析
            text = raw.strip()
            data = _try_parse_any_object(text)
            if data is None:
                # 2) 退化为抽取 JSON 子串再解析
                stripped = _strip_to_json(raw)
                try:
                    data = json.loads(stripped)
                except Exception as exc:
                    raise RuntimeError(
                        f"未找到含必需字段的 JSON 对象；原始前 300 字: {raw[:300]}"
                    ) from exc
                if not isinstance(data, dict):
                    raise RuntimeError(f"JSON 不是对象: {type(data).__name__}")
            for k, default in [
                ("new_character", []),
                ("new_scene", []),
                ("new_event", []),
                ("summary", ""),
            ]:
                data.setdefault(k, default)
            logger.debug(
                f"ch{chapter.get('chapter_id')} 抽取完成 "
                f"({time.time() - t0:.1f}s, attempt {attempt})"
            )
            return data
        except Exception as e:
            last_err = e
            logger.warning(
                f"ch{chapter.get('chapter_id')} 第 {attempt} 次抽取失败: {e}"
            )
            time.sleep(min(2 * attempt, 10))
    logger.error(f"ch{chapter.get('chapter_id')} 抽取彻底失败: {last_err}")
    return _empty_result()


def merge_into_chapter(chapter: dict[str, Any], extracted: dict[str, Any]) -> dict[str, Any]:
    out = dict(chapter)
    out["new_character"] = extracted.get("new_character", [])
    out["new_scene"] = extracted.get("new_scene", [])
    out["new_event"] = extracted.get("new_event", [])
    out["summary"] = extracted.get("summary", "")
    return out
