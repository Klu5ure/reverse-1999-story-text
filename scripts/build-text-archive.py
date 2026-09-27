"""Render the delivered reader assets into a plain-text (Markdown) story archive.

This is a *read-only* consumer of `local/reader-snapshot/`. It never touches the
extraction pipeline, the staging directories or the delivered JSON: it only
reads `directory.json` plus the scene files and writes Markdown.

Design rules
------------
* Reading order is the order of `directory.json`. Nothing is re-derived, and no
  scene, chapter or trail is dropped. Chapters are grouped by `categoryTitle`
  for navigation, but every section keeps its original catalogue index in
  `structured/sections.json`, so the regrouping stays traceable.
* Only a closed whitelist of rich-text tags is removed. `<UTTU>`, `<보안 가이드>`
  and similar strings are literal story text that happens to sit in angle
  brackets, so an unanchored ``<[^>]*>`` strip would silently delete content.
* Missing translations are marked explicitly instead of being filled in with
  another language.

Usage
-----
    python scripts/build-text-archive.py                 # work/text-archive
    python scripts/build-text-archive.py --out DIR
    python scripts/build-text-archive.py --no-placeholder
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "local" / "reader-snapshot"
DEFAULT_OUT = ROOT / "outputs" / "markdown"

# --------------------------------------------------------------------------- #
# Catalogue vocabulary
# --------------------------------------------------------------------------- #

#: Display order of the top-level categories, with their folder slug and label.
CATEGORIES = OrderedDict(
    [
        ("主线剧情", ("mainline", "Main Story")),
        ("活动剧情", ("activity", "Events")),
        ("角色剧情", ("character", "Character Stories")),
        ("玩法故事", ("gameplay", "Gameplay Stories")),
        ("荒原互动", ("wilderness", "Wilderness")),
        ("附加片段", ("extras", "Additional Fragments")),
        ("影像附文", ("pv-notes", "PV Notes")),
    ]
)

#: code -> (line field, section title field, chapter title field, native name, English name)
LANGUAGES = OrderedDict(
    [
        ("zh-CN", ("zh", "title_zh", "chapterTitle", "简体中文", "Chinese (Simplified)")),
        ("zh-TW", ("tw", "title_tw", "chapterTitle_tw", "繁體中文", "Chinese (Traditional)")),
        ("en", ("en", "title_en", "chapterTitle_en", "English", "English")),
        ("ja", ("ja", "title_ja", "chapterTitle_ja", "日本語", "Japanese")),
        ("ko", ("ko", "title_ko", "chapterTitle_ko", "한국어", "Korean")),
    ]
)

TITLE_FIELDS = {
    "title": ("title_zh", "title_tw", "title_en", "title_ja", "title_ko"),
    "chapter": ("chapterTitle", "chapterTitle_tw", "chapterTitle_en", "chapterTitle_ja", "chapterTitle_ko"),
    "category": ("categoryTitle", "categoryTitle_tw", "categoryTitle_en", "categoryTitle_ja", "categoryTitle_ko"),
}

#: Placeholder written when a language has no text for a line that exists in Chinese.
MISSING_MARKER = {
    "zh-CN": "（本语言缺文）",
    "zh-TW": "（本語言缺文）",
    "en": "(text missing in this language)",
    "ja": "（この言語の本文なし）",
    "ko": "(이 언어 본문 없음)",
}

SPEAKER_JOIN = {"zh-CN": "：", "zh-TW": "：", "ja": "：", "en": ": ", "ko": ": "}

#: Shown once for a section whose text is entirely absent in this language,
#: instead of repeating a per-line marker down the whole section.
NOTICE_SECTION = {
    "zh-CN": "（本节没有简体中文文本，原文为繁体中文。）",
    "zh-TW": "（本節沒有繁體中文文本，原文為簡體中文。）",
    "en": "(This section has no English text; the original is in Simplified Chinese.)",
    "ja": "（この節に日本語の本文はありません。原文は簡体字中国語です。）",
    "ko": "(이 절에는 한국어 본문이 없습니다. 원문은 중국어 간체입니다.)",
}

#: Shown once under the heading when a whole chapter lacks this language.
NOTICE_CHAPTER = {
    "zh-CN": "**本章没有简体中文文本。**",
    "zh-TW": "**本章沒有繁體中文文本。**",
    "en": "**This chapter has no English text.**",
    "ja": "**この章に日本語の本文はありません。**",
    "ko": "**이 장에는 한국어 본문이 없습니다.**",
}

#: Language offered as a fallback link when a chapter is untranslated.
FALLBACK_LANGUAGE = "zh-CN"
FALLBACK_NAME = {"zh-CN": "简体中文", "zh-TW": "繁體中文", "en": "English", "ja": "日本語", "ko": "한국어"}
FALLBACK_LINK = {
    "zh-CN": "可阅读 [{name}版]({target})。",
    "zh-TW": "可閱讀 [{name}版]({target})。",
    "en": " Read it in [{name}]({target}).",
    "ja": " [{name}版]({target}) で読めます。",
    "ko": " [{name}]({target})에서 읽을 수 있습니다.",
}

#: Category names per language. The delivered catalogue only carries official
#: translations for two categories, so the rest are project-editorial labels,
#: matching the project's existing practice of editorial grouping.
CATEGORY_LABELS = {
    "主线剧情": {"zh-CN": "主线剧情", "zh-TW": "主線劇情", "en": "Main Story", "ja": "メインストーリー", "ko": "메인 스토리"},
    "活动剧情": {"zh-CN": "活动剧情", "zh-TW": "活動劇情", "en": "Events", "ja": "イベント", "ko": "이벤트"},
    "角色剧情": {"zh-CN": "角色剧情", "zh-TW": "角色劇情", "en": "Character Stories", "ja": "キャラクターストーリー", "ko": "캐릭터 스토리"},
    "玩法故事": {"zh-CN": "玩法故事", "zh-TW": "玩法故事", "en": "Gameplay Stories", "ja": "ゲームプレイストーリー", "ko": "게임플레이 스토리"},
    "荒原互动": {"zh-CN": "荒原互动", "zh-TW": "荒原互動", "en": "Wilderness Interaction", "ja": "ウィルダネス対話", "ko": "황무지 상호작용"},
    "附加片段": {"zh-CN": "附加片段", "zh-TW": "附加片段", "en": "Additional Fragments", "ja": "追加エピソード", "ko": "추가 에피소드"},
    "影像附文": {"zh-CN": "影像附文", "zh-TW": "影像附文", "en": "PV Notes", "ja": "PV付属テキスト", "ko": "PV 부속 텍스트"},
}

#: The catalogue tag that marks a trail, and how each language names it.
TRAIL_TAG = "小径"
TRAIL_LABEL = {"zh-CN": "小径", "zh-TW": "小徑", "en": "Trail", "ja": "小道", "ko": "오솔길"}

#: Interface wording around the story text, per language. The story text itself
#: always comes from the delivered assets; only these wrappers are translated.
UI = {
    "zh-CN": {
        "prev": "上一章", "index": "目录", "next": "下一章",
        "meta": "{category} · 第 {number:03d} 章 · {count} 节",
        "branch": "分支 {number}",
        "empty": "本节无可读文本",
        "typeWrap": "〔{label}：{text}〕",
        "indexTitle": "《重返未来：1999》剧情文本 · 简体中文",
        "indexNote": "阅读语言：简体中文（`zh-CN`）。本索引由维护者的生成流程从游戏剧情数据产出。",
        "indexIntro": [
            "正文含**主线、活动、角色剧情、玩法故事、荒原互动、附加片段、影像附文**七类，小径作为普通小节收入所属章节。",
            "每章文档顶部与底部都有上一章、目录与下一章链接。",
        ],
        "chapters": "- 章：`{chapters}`", "sections": "- 节：`{sections}`",
        "categoryHeader": "## {label} · {count} 章",
        "item": "{count} 节", "itemTrails": "{count} 节，其中小径 {trails} 节",
    },
    "zh-TW": {
        "prev": "上一章", "index": "目錄", "next": "下一章",
        "meta": "{category} · 第 {number:03d} 章 · {count} 節",
        "branch": "分支 {number}",
        "empty": "本節無可讀文本",
        "typeWrap": "〔{label}：{text}〕",
        "indexTitle": "《重返未來：1999》劇情文本 · 繁體中文",
        "indexNote": "閱讀語言：繁體中文（`zh-TW`）。本索引由維護者的生成流程從遊戲劇情資料產出。",
        "indexIntro": [
            "正文含**主線、活動、角色劇情、玩法故事、荒原互動、附加片段、影像附文**七類，小徑作為普通小節收入所屬章節。",
            "每章文件頂部與底部都有上一章、目錄與下一章連結。",
        ],
        "chapters": "- 章：`{chapters}`", "sections": "- 節：`{sections}`",
        "categoryHeader": "## {label} · {count} 章",
        "item": "{count} 節", "itemTrails": "{count} 節，其中小徑 {trails} 節",
    },
    "en": {
        "prev": "Previous chapter", "index": "Contents", "next": "Next chapter",
        "meta": "{category} · Chapter {number:03d} · {count} sections",
        "branch": "Branch {number}",
        "empty": "This section has no readable text.",
        "typeWrap": "[{label}: {text}]",
        "indexTitle": "Reverse: 1999 Story Text · English",
        "indexNote": "Reading language: English (`en`). This index is produced from the game's story data by the maintainer's build.",
        "indexIntro": [
            "The archive covers **main story, events, character stories, gameplay stories, wilderness interaction, additional fragments and PV notes**.",
            "Trails are delivered as ordinary sections inside the chapter they belong to. Every chapter document links to the previous chapter, the contents page and the next chapter.",
        ],
        "chapters": "- Chapters: `{chapters}`", "sections": "- Sections: `{sections}`",
        "categoryHeader": "## {label} · {count} chapters",
        "item": "{count} sections", "itemTrails": "{count} sections, {trails} of them trails",
    },
    "ja": {
        "prev": "前の章", "index": "目次", "next": "次の章",
        "meta": "{category} · 第 {number:03d} 章 · {count} 節",
        "branch": "分岐 {number}",
        "empty": "この節に読める本文はありません。",
        "typeWrap": "〔{label}：{text}〕",
        "indexTitle": "Reverse: 1999 ストーリー本文 · 日本語",
        "indexNote": "閲覧言語：日本語（`ja`）。本索引はゲームのストーリーデータからメンテナのビルドによって生成されています。",
        "indexIntro": [
            "本文は**メインストーリー、イベント、キャラクターストーリー、ゲームプレイストーリー、ウィルダネス対話、追加エピソード、PV付属テキスト**の七分類です。",
            "小道は所属する章の通常の節として収録しています。各章の文書は前の章・目次・次の章へのリンクを上下に備えます。",
        ],
        "chapters": "- 章：`{chapters}`", "sections": "- 節：`{sections}`",
        "categoryHeader": "## {label} · {count} 章",
        "item": "{count} 節", "itemTrails": "{count} 節（うち小道 {trails} 節）",
    },
    "ko": {
        "prev": "이전 장", "index": "목차", "next": "다음 장",
        "meta": "{category} · 제 {number:03d} 장 · {count}개 절",
        "branch": "분기 {number}",
        "empty": "이 절에는 읽을 수 있는 본문이 없습니다.",
        "typeWrap": "[{label}: {text}]",
        "indexTitle": "Reverse: 1999 스토리 본문 · 한국어",
        "indexNote": "읽기 언어: 한국어(`ko`). 이 색인은 게임 스토리 데이터에서 유지관리자의 빌드로 생성됩니다.",
        "indexIntro": [
            "본문은 **메인 스토리, 이벤트, 캐릭터 스토리, 게임플레이 스토리, 황무지 상호작용, 추가 에피소드, PV 부속 텍스트** 일곱 분류입니다.",
            "오솔길은 해당 장의 일반 절로 수록됩니다. 각 장 문서는 위아래에 이전 장·목차·다음 장 링크를 둡니다.",
        ],
        "chapters": "- 장：`{chapters}`", "sections": "- 절：`{sections}`",
        "categoryHeader": "## {label} · {count}개 장",
        "item": "{count}개 절", "itemTrails": "{count}개 절 (오솔길 {trails}개)",
    },
}


CATEGORY_SLUG = {key: value[0] for key, value in CATEGORIES.items()}

TYPE_LABEL = {
    "card": {"zh-CN": "图", "zh-TW": "圖", "en": "image", "ja": "画像", "ko": "이미지"},
    "screen": {"zh-CN": "画面", "zh-TW": "畫面", "en": "scene", "ja": "画面", "ko": "화면"},
}

#: Line types that carry story text and therefore get a "missing" marker.
TEXT_TYPES = frozenset({"dialogue", "narration", "archived"})

# --------------------------------------------------------------------------- #
# Rich-text handling
# --------------------------------------------------------------------------- #

#: Whitelisted markup. Everything outside this list is literal story text.
TAG = re.compile(
    r"""</?(?:size|marktop|i|b|color|align|u|speed|notShowInLog|glitch|sup|sub|brace|nobr|s|sprite|br)"""
    r"""(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^<>]*?))?\s*/?>""",
    re.IGNORECASE,
)

#: A line break, matching the reader's own handling of `<br>`.
LINE_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)

EMPHASIS = (
    (re.compile(r"</?i>", re.IGNORECASE), "*"),
    (re.compile(r"</?b>", re.IGNORECASE), "**"),
    (re.compile(r"</?s>", re.IGNORECASE), "~~"),
)

#: Document layout tokens. `{img}` marks a plate and carries no words, so it
#: leaves nothing behind — the same choice the reader makes, keeping the two in
#: step. Captioned artwork still arrives as a `card` line.
DOC_TOKENS = ((re.compile(r"\{p\}"), "\n\n"), (re.compile(r"\{img\}"), ""))

#: Story text sometimes wraps inner monologue in angle brackets, e.g. `<UTTU>` or
#: `<So the platonic year>`. Markdown renderers parse those as raw HTML and drop
#: them, so only tokens that look like real tags are escaped. CJK and punctuation
#: forms such as `<嗯？>` stay untouched and stay readable in the raw text.
HTML_LIKE = re.compile(
    r"""</?[A-Za-z][A-Za-z0-9-]*(?:\s+[A-Za-z_:][A-Za-z0-9:._-]*"""
    r"""(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>`]+))?)*\s*/?>"""
)


def escape_html_like(text: str) -> str:
    return HTML_LIKE.sub(lambda match: match.group(0).replace("<", "&lt;").replace(">", "&gt;"), text)


def clean_text(raw: str) -> str:
    """Turn one localised field into plain Markdown text."""
    if not raw:
        return ""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = LINE_BREAK.sub("\n", text)
    for pattern, replacement in EMPHASIS:
        text = pattern.sub(replacement, text)
    text = TAG.sub("", text)
    for pattern, replacement in DOC_TOKENS:
        text = pattern.sub(replacement, text)
    text = escape_html_like(text)

    # Leading spaces are layout padding in the newspaper/poster blocks, and four
    # or more would turn the line into a Markdown code block, so drop them all.
    lines = [line.strip() for line in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    # Collapse runs of blank lines, which the newspaper/letter blocks carry.
    out: list[str] = []
    for line in lines:
        if not line and out and not out[-1]:
            continue
        out.append(line)
    return "\n".join(out).strip()


# --------------------------------------------------------------------------- #
# Ordering and grouping
# --------------------------------------------------------------------------- #


def load_catalogue() -> list[dict]:
    with (ASSETS / "directory.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def build_structure(entries: list[dict]) -> list[dict]:
    """Group catalogue entries into chapters, keeping catalogue order inside each."""
    first_seen: OrderedDict[tuple[str, str], int] = OrderedDict()
    for position, entry in enumerate(entries):
        key = (entry.get("categoryTitle") or "", entry.get("chapterTitle") or "")
        first_seen.setdefault(key, position)

    unknown = sorted({category for category, _ in first_seen if category not in CATEGORIES})
    if unknown:
        raise SystemExit(f"unknown categoryTitle values: {unknown}")

    rank = {name: index for index, name in enumerate(CATEGORIES)}
    ordered_keys = sorted(first_seen, key=lambda key: (rank[key[0]], first_seen[key]))

    buckets: dict[tuple[str, str], list[dict]] = {key: [] for key in ordered_keys}
    for position, entry in enumerate(entries):
        key = (entry.get("categoryTitle") or "", entry.get("chapterTitle") or "")
        record = dict(entry)
        record["catalogueIndex"] = position
        buckets[key].append(record)

    chapters: list[dict] = []
    per_category: Counter = Counter()
    for key in ordered_keys:
        category, title = key
        per_category[category] += 1
        members = buckets[key]
        for offset, record in enumerate(members, start=1):
            record["sectionNumber"] = offset
        chapters.append(
            {
                "category": category,
                "slug": CATEGORY_SLUG[category],
                "number": per_category[category],
                "titleKey": title,
                "members": members,
            }
        )
    return chapters


def one_line(text: str) -> str:
    """Collapse embedded newlines and runs of spaces, which some titles carry."""
    return re.sub(r"\s+", " ", text.replace("\u3000", " ")).strip()


def safe_name(text: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", one_line(text))
    return cleaned.strip().rstrip(".") or "chapter"


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def localised(entry: dict, lang: str, kind: str) -> str:
    """Read a localised catalogue field, falling back to Chinese when absent."""
    fields = TITLE_FIELDS[kind]
    index = list(LANGUAGES).index(lang)
    value = one_line(entry.get(fields[index]) or "")
    return value or one_line(entry.get(fields[0]) or "")


def render_line(line: dict, lang: str, stats: Counter) -> list[str] | None:
    """Render one story line, or None when it is handled by the walker itself."""
    code = LANGUAGES[lang][0]
    kind = line.get("type") or "dialogue"
    if kind == "choice":
        return None
    text = clean_text(line.get(code) or "")

    if not text:
        chinese = clean_text(line.get("zh") or "")
        if not chinese:
            # Nothing in any language, including empty card captions.
            return []
        stats["missing"] += 1
        if not stats["placeholder"]:
            return []
        marker = MISSING_MARKER[lang]
        if kind in ("card", "screen"):
            return [f"*{UI[lang]['typeWrap'].format(label=TYPE_LABEL[kind][lang], text=marker)}*"]
        if kind not in TEXT_TYPES:
            return []
        return [f"*{marker}*"]

    stats["rendered"] += 1
    if kind in ("card", "screen"):
        return [f"*{UI[lang]['typeWrap'].format(label=TYPE_LABEL[kind][lang], text=text)}*"]
    speaker = clean_text(line.get(f"spk_{code}") or "")
    if speaker:
        return [f"**{speaker}**{SPEAKER_JOIN[lang]}{text}"]
    if kind == "archived":
        return [f"*{text}*"]
    return [text]


def option_text(option: dict, lang: str) -> str:
    code = LANGUAGES[lang][0]
    return clean_text(option.get(code) or "") or clean_text(option.get("zh") or "")


def option_target(option: dict):
    runtime = option.get("runtime") or {}
    return runtime.get("target", option.get("target"))


def option_is_continue(option: dict) -> bool:
    return (option.get("runtime") or {}).get("type") == "continue"


def walk_scene(scene: dict) -> list[tuple[str, object]]:
    """Order one scene the way the reader does.

    Mirrors ``readingPath`` in `app/story-model.ts`: follow `entryNodeId` along
    verified edges rather than the raw `lines` array, which is in source order
    and is frequently permuted or even reversed. Where the reader would stop at
    a choice, the walker instead descends into every option so the archive keeps
    the whole scene, labelling each branch. Nodes already emitted are skipped so
    branches that merge do not repeat their shared tail.
    """
    nodes = {node["id"]: node for node in scene["nodes"]}
    index_of = {event["id"]: i for i, event in enumerate(scene["lines"])}
    items: list[tuple[str, object]] = []
    emitted_nodes: set[int] = set()
    budget = 8 * len(scene["lines"]) + 200
    guard = {"emitted": 0}

    def emit(event_id: str) -> None:
        index = index_of.get(event_id)
        if index is None or guard["emitted"] >= budget:
            return
        guard["emitted"] += 1
        items.append(("line", index))

    def walk(start: int | None, allow_revisit: bool, depth: int = 0, prefix: str = "") -> None:
        if start is None or depth > 64:
            return
        on_chain: set[int] = set()
        cursor: int | None = start
        while cursor is not None:
            if guard["emitted"] >= budget or cursor in on_chain:
                return
            on_chain.add(cursor)
            if cursor in emitted_nodes and not allow_revisit:
                return
            emitted_nodes.add(cursor)
            node = nodes.get(cursor)
            if node is None:
                return

            events = node.get("events", [])
            for event_id in events:
                emit(event_id)

            choice = None
            for event_id in events:
                index = index_of.get(event_id)
                if index is not None and scene["lines"][index].get("type") == "choice":
                    choice = scene["lines"][index]
                    break
            if choice is not None:
                options = [opt for opt in (choice.get("options") or []) if option_target(opt) in nodes]
                items.append(("options", choice))
                for number, option in enumerate(options, start=1):
                    if len(options) > 1:
                        # Number by nesting depth, so an option inside another
                        # branch reads as 1.2 rather than looking like a sibling.
                        label = f"{prefix}.{number}" if prefix else str(number)
                        items.append(("branch", (label, option)))
                    else:
                        label = prefix
                    walk(option_target(option), allow_revisit or option_is_continue(option), depth + 1, label)
                return

            edges = node.get("edges") or []
            if len(edges) > 1:
                # Unverified branch: the reader refuses to guess, and so do we.
                return
            cursor = edges[0]["target"] if edges else None

    walk(scene.get("entryNodeId"), False)
    return items


def render_section(entry: dict, scene: dict, lang: str, stats: Counter) -> tuple[str, list[str], dict]:
    """Return (heading, body lines, info) for one section."""
    title = localised(entry, lang, "title")
    tag = entry.get("tag") or ""
    shown_tag = TRAIL_LABEL[lang] if tag == TRAIL_TAG else tag
    heading = f"## {entry['sectionNumber']:02d} · {shown_tag} · {title}" if shown_tag else f"## {entry['sectionNumber']:02d} · {title}"

    items = walk_scene(scene)
    lines = scene.get("lines", [])
    code = LANGUAGES[lang][0]
    emitted = {payload for kind, payload in items if kind == "line"}

    # Card and screen captions are labels, not prose; a section with captions but
    # no translated dialogue still counts as untranslated.
    prose = [index for index in emitted if (lines[index].get("type") or "dialogue") in TEXT_TYPES]
    translated_prose = sum(1 for index in prose if clean_text(lines[index].get(code) or ""))
    chinese_prose = sum(1 for index in prose if clean_text(lines[index].get("zh") or ""))

    archived = sum(1 for line in lines if line.get("type") == "archived")
    unwalked = [i for i in range(len(lines)) if i not in emitted and lines[i].get("type") != "archived"]
    stats["linesEmitted"] += len(emitted)
    stats["linesTotal"] += len(lines)
    stats["linesArchived"] += archived
    stats["linesUnwalked"] += len(unwalked)

    info = {
        "linesTotal": len(lines),
        "linesEmitted": len(emitted),
        "linesArchived": archived,
        "linesUnwalked": len(unwalked),
        "branches": 0,
        "translatedProse": translated_prose,
        "collapsed": bool(emitted) and not translated_prose and bool(chinese_prose),
        "unwalkedIds": [lines[i]["id"] for i in unwalked[:20]],
    }

    if info["collapsed"]:
        stats["missing"] += chinese_prose
        stats["collapsedSections"] += 1
        return heading, [f"*{NOTICE_SECTION[lang]}*"], info

    # Every spoken line becomes its own paragraph. Left as consecutive lines
    # Markdown would join them into one flowing block, which is exactly what the
    # first version did and what made the chapter unreadable.
    body: list[str] = []
    branches = 0
    for kind, payload in items:
        if kind == "line":
            rendered = render_line(lines[payload], lang, stats)  # type: ignore[index]
            for chunk in rendered or []:
                body.extend(piece for piece in chunk.split("\n") if piece.strip())
        elif kind == "options":
            # Kept together so the choices stay one list.
            rows = []
            for option in payload.get("options") or []:  # type: ignore[union-attr]
                label = option_text(option, lang)
                rows.append(f"- {label}" if label else f"- *{MISSING_MARKER[lang]}*")
            if rows:
                body.append("\n".join(rows))
        else:
            branches += 1
            number, option = payload  # type: ignore[misc]
            label = option_text(option, lang)  # type: ignore[arg-type]
            marker = UI[lang]["branch"].format(number=number)
            body.append(f"**〔{marker}：{label}〕**" if label else f"**〔{marker}〕**")

    info["branches"] = branches
    if not body:
        body = [f"*（{UI[lang]['empty']}）*"]
    return heading, body, info


def link_path(path: str) -> str:
    """Percent-encode what a bare Markdown destination may not contain.

    GitHub will not treat `[text](a b.md)` as a link at all — a space ends the
    destination — so chapter names with spaces have to be escaped. Only the
    characters that actually break parsing are touched, which keeps the raw
    Markdown readable.
    """
    for char, escape in ((" ", "%20"), ("(", "%28"), (")", "%29"), ("<", "%3C"), (">", "%3E")):
        path = path.replace(char, escape)
    return path


def nav_bar(lang: str, prev_link: str | None, index_link: str, next_link: str | None) -> str:
    words = UI[lang]
    parts = [
        f"[← {words['prev']}]({link_path(prev_link)})" if prev_link else f"← {words['prev']}",
        f"[{words['index']}]({link_path(index_link)})",
        f"[{words['next']} →]({link_path(next_link)})" if next_link else f"{words['next']} →",
    ]
    return " · ".join(parts)


def prepare_language(chapters: list[dict], lang: str) -> None:
    """Give every chapter its per-language file name and display title."""
    used: dict[tuple[str, str], int] = {}
    for chapter in chapters:
        title = localised(chapter["members"][0], lang, "chapter")
        base = f"{chapter['number']:03d}-{safe_name(title)}"
        seen = used.get((chapter["slug"], base), 0)
        used[(chapter["slug"], base)] = seen + 1
        suffix = f"-{seen + 1}" if seen else ""
        chapter["fileName"] = f"{base}{suffix}.md"
        chapter["displayTitle"] = title


def build_language(
    chapters: list[dict], lang: str, out: Path, stats: Counter, names: dict[str, list[str]]
) -> tuple[dict, list[dict]]:
    prepare_language(chapters, lang)
    lang_dir = out / lang
    lang_dir.mkdir(parents=True, exist_ok=True)
    section_report: list[dict] = []

    def link(chapter: dict) -> str:
        return f"../{chapter['slug']}/{chapter['fileName']}"

    fallback = FALLBACK_LANGUAGE if FALLBACK_LANGUAGE in names and FALLBACK_LANGUAGE != lang else None

    written: set[Path] = set()
    for position, chapter in enumerate(chapters):
        prev_chapter = chapters[position - 1] if position else None
        next_chapter = chapters[position + 1] if position + 1 < len(chapters) else None
        top = nav_bar(
            lang,
            link(prev_chapter) if prev_chapter else None,
            "../README.md",
            link(next_chapter) if next_chapter else None,
        )
        blocks = [
            f"# {chapter['displayTitle']}",
            "",
            "> "
            + UI[lang]["meta"].format(
                category=category_label(chapter["category"], lang, chapter["members"]),
                number=chapter["number"],
                count=len(chapter["members"]),
            ),
            "",
            top,
            "",
        ]

        rendered_sections: list[tuple[str, list[str], dict]] = []
        for entry in chapter["members"]:
            with (ASSETS / f"{entry['id']}.json").open(encoding="utf-8") as handle:
                scene = json.load(handle)
            heading, body, info = render_section(entry, scene, lang, stats)
            rendered_sections.append((heading, body, info))
            if info["linesUnwalked"] or info["branches"]:
                section_report.append({"id": entry["id"], "language": lang, **info})

        # When nothing in the chapter is translated, say so once and keep only the
        # section headings. A notice under all 47 headings is still a wall of noise.
        untranslated = sum(info["translatedProse"] for _h, _b, info in rendered_sections) == 0
        if untranslated and rendered_sections:
            notice = NOTICE_CHAPTER[lang]
            if fallback:
                target = link_path(f"../../{fallback}/{chapter['slug']}/{names[fallback][position]}")
                notice += FALLBACK_LINK[lang].format(name=FALLBACK_NAME[fallback], target=target)
            blocks.extend([notice, ""])
            for heading, _body, _info in rendered_sections:
                blocks.extend([heading, ""])
        else:
            for heading, body, _info in rendered_sections:
                blocks.append(heading)
                blocks.append("")
                blocks.append("\n\n".join(body))
                blocks.append("")
        blocks.extend(["---", "", top, ""])

        target = lang_dir / chapter["slug"] / chapter["fileName"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(blocks), encoding="utf-8", newline="\n")
        written.add(target.resolve())

    # Remove chapter files left behind by an earlier run whose naming changed,
    # so repeated runs converge instead of accumulating stale documents.
    stale = 0
    for slug in CATEGORY_SLUG.values():
        folder = lang_dir / slug
        if not folder.is_dir():
            continue
        for candidate in folder.glob("*.md"):
            if candidate.resolve() not in written:
                candidate.unlink()
                stale += 1

    (lang_dir / "README.md").write_text(build_index(chapters, lang), encoding="utf-8", newline="\n")
    return (
        {
            "files": len(chapters),
            "sections": sum(len(ch["members"]) for ch in chapters),
            "staleRemoved": stale,
        },
        section_report,
    )


def category_label(category: str, lang: str, members: list[dict] | None = None) -> str:
    """Prefer the catalogue's own translation, fall back to the editorial label."""
    if members:
        field = TITLE_FIELDS["category"][list(LANGUAGES).index(lang)]
        for member in members:
            value = one_line(member.get(field) or "")
            if value:
                return value
    return CATEGORY_LABELS[category][lang]


def build_index(chapters: list[dict], lang: str) -> str:
    words = UI[lang]
    lines = [
        f"# {words['indexTitle']}",
        "",
        f"> {words['indexNote']}",
        "",
    ]
    lines.extend(words["indexIntro"])
    lines.extend(
        [
            "",
            words["chapters"].format(chapters=len(chapters)),
            words["sections"].format(sections=sum(len(ch["members"]) for ch in chapters)),
            "",
        ]
    )
    for category, (_slug, _label) in CATEGORIES.items():
        group = [ch for ch in chapters if ch["category"] == category]
        if not group:
            continue
        lines.append(words["categoryHeader"].format(label=CATEGORY_LABELS[category][lang], count=len(group)))
        lines.append("")
        for chapter in group:
            count = len(chapter["members"])
            trails = sum(1 for member in chapter["members"] if member.get("tag") == TRAIL_TAG)
            detail = words["itemTrails"].format(count=count, trails=trails) if trails else words["item"].format(count=count)
            target = link_path(f"{chapter['slug']}/{chapter['fileName']}")
            lines.append(f"- [{chapter['number']:03d} · {chapter['displayTitle']}]({target}) — {detail}")
        lines.append("")
    return "\n".join(lines)


def build_root_readme(chapters: list[dict], lang_stats: dict[str, dict]) -> str:
    total_sections = sum(len(ch["members"]) for ch in chapters)
    lines = [
        "# 重返未来：1999 剧情文本",
        "",
        "这里是《重返未来：1999》的剧情文本，一共五种语言：简体中文、繁體中文、英文、日文、韩文。",
        "",
        "一章一个 Markdown 文件，在浏览器里点开就能看。每章的开头和结尾都有上一章、目录、下一章的链接，从头顺着读就行。",
        "",
        "**内容包含剧透**，主线、活动、角色剧情都在里面，结局也没删掉。建议按自己玩到哪儿再看。",
        "",
        "## 阅读入口",
        "",
        "| 语言 | 目录 |",
        "| --- | --- |",
    ]
    for lang, meta in LANGUAGES.items():
        if lang not in lang_stats:
            continue
        lines.append(f"| {meta[3]} | [{lang}/README.md]({lang}/README.md) |")
    lines += [
        "",
        f"每种语言都是 {len(chapters)} 章、{total_sections} 节。",
        "",
        "## 收录了什么",
        "",
        "正文分成七类：主线、活动、角色剧情、玩法故事、荒原互动、附加片段、影像附文。",
        "",
        "小径是章节地图上的支线读物，报纸、公告、信件这一类。它没有单独放，而是按普通小节排进所属的章节里，",
        "读剧情的时候顺路就能看到。",
        "",
        "## 顺序是怎么排的",
        "",
        "文本来自游戏剧情数据。段落顺序按游戏里的剧情走向排，不是配置文件里的行序——",
        "后者常常被打乱，有时候整个是反的，照着读会前后颠倒。",
        "",
        "剧情里的选项就地展开，每条分支都标出来。分支重新汇合之后的共同结尾只出现一次，不会重复。",
        "",
        "章节归到哪一类、哪一章，用的是同一份目录数据，没有另外推断。每节的原始编号记在 `structured/sections.json` 里，",
        "可以逐条核对。",
        "",
        "## 有哪些缺口",
        "",
        "各语言的资源并不完全对等，有些章节或小节在某种语言下没有正文。这种情况只写一句说明，",
        "不会拿别的语言顶上，也不会整页刷占位符。只有零星缺句才逐行标出。统计在 `structured/language-gaps.json`。",
        "",
        "游戏里不会显示的日志备用行没有收进来。正文里的图片和画面用 `〔图：…〕`、`〔画面：…〕` 标出，图片本身不在仓库里。",
        "",
        "字号、颜色、注音这些排版标记已经去掉了，`<i>`、`<b>`、`<s>` 换成了 Markdown 的强调写法。",
        "",
        "## 版权",
        "",
        "本仓库由玩家整理，非官方、非商业，与游戏开发商、发行商以及其他权利人没有关系。",
        "《重返未来：1999》的名称、角色和剧情文本，权利归各自权利人所有。",
        "",
        "这里只用于保存资料、检索和个人阅读，不主张任何权利，也不可用于商业用途。权利人如果认为有不妥，请联系。",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    parser.add_argument(
        "--no-placeholder",
        dest="placeholder",
        action="store_false",
        help="omit the marker for lines a language is missing",
    )
    parser.add_argument("--langs", default=",".join(LANGUAGES), help="comma separated language codes")
    args = parser.parse_args(argv)

    if not (ASSETS / "directory.json").exists():
        print(f"missing {ASSETS / 'directory.json'}", file=sys.stderr)
        return 2

    wanted = [code.strip() for code in args.langs.split(",") if code.strip()]
    unknown = [code for code in wanted if code not in LANGUAGES]
    if unknown:
        print(f"unknown languages: {unknown}", file=sys.stderr)
        return 2

    out: Path = args.out if args.out.is_absolute() else ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)

    chapters = build_structure(load_catalogue())

    # File names are language specific; resolve them for every generated
    # language up front so cross-language fallback links are stable.
    names: dict[str, list[str]] = {}
    for code in wanted:
        prepare_language(chapters, code)
        names[code] = [chapter["fileName"] for chapter in chapters]

    lang_stats: dict[str, dict] = {}
    gaps: dict[str, dict] = {}
    coverage: dict[str, list[dict]] = {}
    for lang in wanted:
        stats: Counter = Counter(placeholder=args.placeholder)
        info, section_report = build_language(chapters, lang, out, stats, names)
        info["linesMissing"] = stats["missing"]
        info["linesRendered"] = stats["rendered"]
        info["linesEmitted"] = stats["linesEmitted"]
        info["linesTotal"] = stats["linesTotal"]
        info["linesArchived"] = stats["linesArchived"]
        info["linesUnwalked"] = stats["linesUnwalked"]
        info["untranslatedSections"] = stats["collapsedSections"]
        lang_stats[lang] = info
        coverage[lang] = section_report
        gaps[lang] = {
            "sections": info["sections"],
            "linesRendered": stats["rendered"],
            "linesMissing": stats["missing"],
            "untranslatedSections": stats["collapsedSections"],
            "linesTotalInScenes": stats["linesTotal"],
            "linesReachable": stats["linesEmitted"],
            "linesArchivedExcluded": stats["linesArchived"],
            "linesUnwalked": stats["linesUnwalked"],
        }

    (out / "README.md").write_text(build_root_readme(chapters, lang_stats), encoding="utf-8", newline="\n")

    structured = out / "structured"
    structured.mkdir(parents=True, exist_ok=True)
    (structured / "chapters.json").write_text(
        json.dumps(
            [
                {
                    "category": chapter["category"],
                    "slug": chapter["slug"],
                    "number": chapter["number"],
                    "title": chapter["titleKey"],
                    "sectionCount": len(chapter["members"]),
                    "trailCount": sum(1 for member in chapter["members"] if member.get("tag") == TRAIL_TAG),
                    "sectionIds": [member["id"] for member in chapter["members"]],
                }
                for chapter in chapters
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (structured / "sections.json").write_text(
        json.dumps(
            [
                {
                    "id": member["id"],
                    "tag": member.get("tag") or "",
                    "category": chapter["category"],
                    "slug": chapter["slug"],
                    "chapter": chapter["titleKey"],
                    "chapterNumber": chapter["number"],
                    "sectionNumber": member["sectionNumber"],
                    "catalogueIndex": member["catalogueIndex"],
                }
                for chapter in chapters
                for member in chapter["members"]
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (structured / "language-gaps.json").write_text(
        json.dumps(gaps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (structured / "scene-coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )

    print(f"chapters : {len(chapters)}")
    print(f"sections : {sum(len(ch['members']) for ch in chapters)}")
    for lang in wanted:
        info = lang_stats[lang]
        stale = f" staleRemoved={info['staleRemoved']}" if info["staleRemoved"] else ""
        print(
            f"  {lang:<6} files={info['files']:<4} rendered={info['linesRendered']:<7} "
            f"missing={info['linesMissing']:<6} untranslatedSections={info['untranslatedSections']:<5} "
            f"archived={info['linesArchived']:<6} unwalked={info['linesUnwalked']}{stale}"
        )
    print(f"output   : {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
