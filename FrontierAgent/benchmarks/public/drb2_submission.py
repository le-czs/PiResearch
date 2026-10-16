"""DeepResearch Bench II submission contract.

The judge scores the exported Markdown, not whatever the agent wrote to
``/outputs``. V4-class models often park the real report in the sandbox and
leave a pointer in the final message. This module picks the deliverable that
should be graded, without reading hidden rubrics.
"""

from __future__ import annotations

import re
from pathlib import Path

MAX_REPORT_CHARS = 150_000
_SHORT_CHAT_RATIO = 0.4

_POINTER_RE = re.compile(
    r"("
    r"写入\s*[`'\"]?/outputs"
    r"|written to\s+[`'\"]?/outputs"
    r"|saved (?:to|at|under)\s+[`'\"]?/outputs"
    r"|sits alone in [`'\"]?/outputs"
    r"|report is (?:complete and )?ready"
    r"|no further action"
    r"|final deliverable sits"
    r"|报告已完成"
    r"|the report is complete"
    r")",
    re.IGNORECASE,
)

_ITEM_RE = re.compile(
    r"^(?:(?:\d+|[A-Za-z])[\.\)、]\s+|[-*]\s+)(.+)$",
    re.MULTILINE,
)

_COUNTRY_RE = re.compile(
    r"\b("
    r"Indonesia|Malaysia|Pakistan|the Philippines|Philippines|Sri Lanka|"
    r"Thailand|Vietnam|India|Bangladesh|Nepal|Japan|South Korea|"
    r"Republic of Korea|Korea|Singapore|China"
    r")\b"
)

PROMPT_ADDENDUM = """
## Submission contract (mandatory)

Work in three phases. Do not skip phase 2.

Phase 1 — research. Cover every numbered or lettered deliverable in the
user prompt: requested tables, named countries or programs, and explicit
figures. Write notes under `/outputs/notes/`.

Phase 2 — gap fill (mandatory even if the task board is all green). Compare
notes against the required-deliverables list. Spawn sub-agents only for
missing numbers, empty table cells, named countries/programs/papers, or
analysis questions that still lack sources. Do not rewrite covered sections.
If a fetch returns `[ERROR]` or "Scraping failed", do not retry that URL;
switch source or use the search snippet.

Phase 3 — write the graded report. Your last message must be the complete
Markdown report, and you must also write it to `/outputs/report.md`. A path,
a completion note, or an outline is not a submission.

Length and evidence:
- Chinese reports: at least 12,000 Chinese characters of body text, with
  analysis paragraphs after every table.
- English reports: at least 25,000 characters, same rule.
- Every key figure needs a source URL or official document name.
- Expand analysis: why the number matters, where sources disagree, and
  what is still uncertain.

If the prompt forbids a paper or URL, that prohibition outranks every other
instruction. Do not open, quote, or paraphrase that source.
""".strip()


def looks_like_pointer(text: str) -> bool:
    """True when the chat answer is a status note rather than a report."""
    chat = (text or "").strip()
    if not chat:
        return True
    if len(chat) >= 8000:
        return False
    return bool(_POINTER_RE.search(chat[:1200]))


def longest_markdown(outputs_dir: Path | None, *, max_chars: int = MAX_REPORT_CHARS) -> str:
    """Return the longest Markdown/text file under a sandbox outputs directory."""
    if outputs_dir is None or not outputs_dir.is_dir():
        return ""
    candidates = [
        path
        for path in outputs_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".md", ".txt"}
    ]
    if not candidates:
        return ""
    best = max(candidates, key=lambda path: path.stat().st_size)
    return best.read_text(encoding="utf-8", errors="replace")[:max_chars]


def select_submission(
    chat_answer: str,
    outputs_dir: Path | None,
    *,
    max_chars: int = MAX_REPORT_CHARS,
) -> tuple[str, str]:
    """Choose the graded report from the chat answer and sandbox outputs.

    Prefers the sandbox Markdown when the chat answer is a pointer or is
    much shorter than the file. When both look like full reports, keep the
    longer one.
    """
    chat = (chat_answer or "").strip()
    sandbox = longest_markdown(outputs_dir, max_chars=max_chars)
    if sandbox and (looks_like_pointer(chat) or not chat or len(chat) < _SHORT_CHAT_RATIO * len(sandbox)):
        return sandbox, "sandbox"
    if sandbox and len(sandbox) > len(chat):
        return sandbox, "sandbox"
    return chat, "chat"


def coverage_items_from_prompt(question: str) -> list[str]:
    """Extract public deliverables from the prompt. Never reads hidden rubrics."""
    items: list[str] = []
    seen: set[str] = set()

    def _add(text: str) -> None:
        cleaned = re.sub(r"\s+", " ", text).strip()
        key = cleaned.lower()
        if len(cleaned) < 8 or key in seen:
            return
        seen.add(key)
        items.append(cleaned[:240])

    for match in _ITEM_RE.finditer(question or ""):
        _add(match.group(1))
    countries = []
    for match in _COUNTRY_RE.finditer(question or ""):
        name = match.group(1)
        if name.lower() == "korea":
            name = "South Korea"
        if name.lower() == "the philippines":
            name = "Philippines"
        if name not in countries:
            countries.append(name)
    if len(countries) >= 2:
        for name in countries:
            _add(f"Cover {name} with the fields requested in the prompt.")
    return items


def render_coverage_block(question: str) -> str:
    items = coverage_items_from_prompt(question)
    if not items:
        return ""
    lines = [
        "## Required deliverables from the prompt",
        "",
        "Cover every item before finishing. This list is derived only from the "
        "user prompt, not from any hidden rubric.",
        "",
    ]
    lines.extend(f"{index}. {item}" for index, item in enumerate(items, start=1))
    return "\n".join(lines)


def render_instruction_addendum(question: str) -> str:
    parts = [PROMPT_ADDENDUM]
    coverage = render_coverage_block(question)
    if coverage:
        parts.append(coverage)
    return "\n\n".join(parts)


def persist_selected_report(text: str, outputs_dir: Path | None) -> Path | None:
    """Write the graded report to ``/outputs/report.md`` when a body exists."""
    body = (text or "").strip()
    if not body or outputs_dir is None:
        return None
    outputs_dir.mkdir(parents=True, exist_ok=True)
    path = outputs_dir / "report.md"
    path.write_text(body[:MAX_REPORT_CHARS], encoding="utf-8")
    return path
