from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from meeting_scribe.config import LlmConfig
from meeting_scribe.domain.models import SummaryResult


class SummaryClient:
    def __init__(self, config: LlmConfig) -> None:
        self.config = config

    def summarize(self, transcript_md: str) -> SummaryResult:
        chunks = _split_transcript(
            transcript_md, self.config.max_transcript_chars_per_chunk
        )
        if len(chunks) == 1:
            return self._summarize_once(chunks[0])
        partials = [self._summarize_once(chunk) for chunk in chunks]
        combined = '\n\n'.join(
            f'## Bölüm {index}\n\n{result.summary_md}'
            for index, result in enumerate(partials, start=1)
        )
        return self._summarize_once(combined)

    def _summarize_once(self, transcript_md: str) -> SummaryResult:
        api_key = os.getenv(self.config.api_key_env)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": "Return strict JSON for a meeting summary."},
                {"role": "user", "content": _summary_prompt(transcript_md)},
            ],
            "response_format": {"type": "json_object"},
        }
        with httpx.Client(timeout=self.config.timeout_seconds) as client:
            response = client.post(f"{self.config.base_url.rstrip('/')}/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return result_from_json(json.loads(content))


class StubSummaryClient:
    def summarize(self, transcript_md: str) -> SummaryResult:
        return SummaryResult(
            summary_md=(
                "# Kısa Özet\n\n"
                "LLM entegrasyonu yapılandırılmadı; bu stub çıktıdır.\n\n"
                "## Görüşülen Konular\n- Yok\n\n"
                "## Alınan Kararlar\n- Yok\n\n"
                "## Aksiyonlar\n- Yok\n\n"
                "## Açık Sorular\n- Yok\n"
            ),
            discussed_topics=[],
            decisions=[],
            actions=[],
            open_questions=[],
        )


def result_from_json(payload: dict[str, Any]) -> SummaryResult:
    paragraphs = (
        payload.get("short_summary")
        or payload.get("summary")
        or payload.get("summary_paragraphs")
        or []
    )
    if isinstance(paragraphs, str):
        summary_text = paragraphs
    else:
        summary_text = "\n\n".join(str(item) for item in paragraphs)
    discussed_topics = _string_list(
        payload.get("discussed_topics") or payload.get("topics") or []
    )
    decisions = _string_list(payload.get("decisions") or [])
    actions = _normalize_actions(payload.get("actions") or [])
    open_questions = _string_list(
        payload.get("open_questions") or payload.get("open_topics") or []
    )
    summary_md = render_summary_markdown(
        summary_text,
        discussed_topics,
        decisions,
        actions,
        open_questions,
    )
    return SummaryResult(
        summary_md,
        discussed_topics,
        decisions,
        actions,
        open_questions,
    )


def render_summary_markdown(
    summary_text: str,
    discussed_topics: list[str],
    decisions: list[str],
    actions: list[dict[str, str]],
    open_questions: list[str],
) -> str:
    lines = [
        "# Kısa Özet",
        "",
        summary_text.strip() or "Özet yok.",
        "",
        "## Görüşülen Konular",
    ]
    lines.extend(_list_items(discussed_topics))
    lines.extend(["", "## Alınan Kararlar"])
    lines.extend(_list_items(decisions))
    lines.extend(["", "## Aksiyonlar"])
    lines.extend(_action_items(actions))
    lines.extend(["", "## Açık Sorular"])
    lines.extend(_list_items(open_questions))
    return "\n".join(lines) + "\n"


def write_summary(path: Path, result: SummaryResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.summary_md, encoding="utf-8")


def _list_items(items: list[str]) -> list[str]:
    return [f"- {item}" for item in items] or ["- Yok"]


def _action_items(actions: list[dict[str, str]]) -> list[str]:
    if not actions:
        return ["- Yok"]
    lines: list[str] = []
    for action in actions:
        lines.extend(
            [
                f"- Görev: {action['task']}",
                f"  - Sorumlu: {action['owner']}",
                f"  - Termin: {action['deadline']}",
            ]
        )
    return lines


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = _first_text(
                item,
                'text',
                'topic',
                'decision',
                'question',
                'description',
            )
            if not text:
                text = json.dumps(item, ensure_ascii=False)
        else:
            text = str(item).strip()
        if text:
            items.append(text)
    return items


def _normalize_actions(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    actions: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, str):
            task = item.strip()
            owner = deadline = 'Belirtilmedi'
        elif isinstance(item, dict):
            task = _first_text(
                item,
                'task',
                'görev',
                'gorev',
                'action',
                'aksiyon',
                'description',
            )
            owner = _first_text(
                item,
                'owner',
                'sorumlu',
                'assignee',
            ) or 'Belirtilmedi'
            deadline = _first_text(
                item,
                'deadline',
                'termin',
                'due_date',
                'dueDate',
            ) or 'Belirtilmedi'
        else:
            continue
        if task:
            actions.append(
                {'task': task, 'owner': owner, 'deadline': deadline}
            )
    return actions


def _first_text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ''


def _summary_prompt(transcript_md: str) -> str:
    return (
        "Aşağıdaki toplantı transkriptini özetle ve yalnızca geçerli JSON üret. "
        "Şema tam olarak şöyle olmalı:\n"
        '{"short_summary":"...","discussed_topics":["..."],'
        '"decisions":["..."],"actions":['
        '{"task":"...","owner":"...","deadline":"..."}],'
        '"open_questions":["..."]}.\n'
        "Kısa özet öz ve bilgi yoğun olsun. Görüşülen konuları, açıkça alınan "
        "kararları ve cevaplanmamış soruları birbirinden ayır. Aksiyonlarda görev, "
        "sorumlu ve termini ayrı alanlara yaz. Transkriptte sorumlu veya termin "
        "söylenmemişse kesinlikle uydurma; ilgili alana 'Belirtilmedi' yaz. "
        "Konuşmada bulunmayan hiçbir bilgi ekleme. JSON dışında metin üretme.\n\n"
        f"{transcript_md}"
    )


def _split_transcript(text: str, max_chars: int) -> list[str]:
    if max_chars < 1000:
        raise ValueError('max_transcript_chars_per_chunk must be at least 1000')
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in text.splitlines(keepends=True):
        if current and current_length + len(line) > max_chars:
            chunks.append(''.join(current))
            current = []
            current_length = 0
        while len(line) > max_chars:
            room = max_chars - current_length
            current.append(line[:room])
            chunks.append(''.join(current))
            current = []
            current_length = 0
            line = line[room:]
        current.append(line)
        current_length += len(line)
    if current:
        chunks.append(''.join(current))
    return chunks
