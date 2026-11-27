"""Markdown note chunker, spec 13.4."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..prompts import clean_controls

MAX_CHUNK_TOKENS = 300
OVERLAP = 0.15

_DATE_VALUE_RE = re.compile(r"""^\s*["']?(\d{4}-\d{2}-\d{2})(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?"""
                            r"""(?:Z|[+-]\d{2}:?\d{2})?["']?\s*$""")
_FILENAME_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[\s_-]+(.*))?$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_SENT_RE = re.compile(r"(?<=[.!?])\s+")
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def approx_tokens(text: str) -> int:
    """Fallback token estimate; production passes the EMBED server's /tokenize count (13.4.7)."""
    return max(1, (len(re.findall(r"\w+|[^\w\s]", text)) * 5 + 3) // 4) if text.strip() else 0


@dataclass
class NoteMeta:
    title: str
    declared_date: Optional[str]
    event_date: Optional[str]
    rank_date: str
    date_source: str
    frontmatter: dict


@dataclass
class Chunk:
    ord: int
    header: str
    body: str
    title: str
    breadcrumb: str
    token_count: int
    text_hash: str = ""

    def __post_init__(self):
        self.text_hash = hashlib.sha256((self.header + "\n" + self.body).encode()).hexdigest()


# ---- 13.4.1 frontmatter ---------------------------------------------------------------------
def split_frontmatter(text: str) -> tuple[dict, str]:
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() in ("---", "..."))
    except StopIteration:
        return {}, text
    fm: dict = {}
    raw: list[str] = []
    key = None
    for line in lines[1:end]:
        m = re.match(r"^([A-Za-z0-9_\-]+):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val == "":
                fm[key] = []
            elif val.startswith("[") and val.endswith("]"):
                fm[key] = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
            else:
                fm[key] = val.strip("'\"") if not _DATE_VALUE_RE.match(val) else val
            continue
        m = re.match(r"^\s+-\s+(.*)$", line)
        if m and key is not None and isinstance(fm.get(key), list):
            fm[key].append(m.group(1).strip().strip("'\""))
            continue
        if line.strip():
            raw.append(line)
    if raw:
        fm["_raw"] = raw
    return fm, "\n".join(lines[end + 1:])


def _fm_date(fm: dict, *names) -> Optional[str]:
    for n in names:
        v = fm.get(n)
        if isinstance(v, str):
            m = _DATE_VALUE_RE.match(v)
            if m:
                try:
                    _dt.date.fromisoformat(m.group(1))
                    return m.group(1)
                except ValueError:
                    pass
    return None


# ---- 13.4.2 dates and title -----------------------------------------------------------------
def note_meta(relpath: str, fm: dict, mtime_date: str, cap: str) -> NoteMeta:
    stem = relpath.rsplit("/", 1)[-1]
    stem = stem[:-3] if stem.lower().endswith(".md") else stem
    file_date = None
    title = stem
    m = _FILENAME_DATE_RE.match(stem)
    if m:
        try:
            _dt.date.fromisoformat(m.group(1))
            file_date = m.group(1)
            title = (m.group(2) or "").strip() or "Daily note"
        except ValueError:
            pass
    declared = _fm_date(fm, "updated", "modified") or _fm_date(fm, "date", "created") or file_date
    source = "frontmatter" if (_fm_date(fm, "updated", "modified") or _fm_date(fm, "date", "created")) else (
        "filename" if file_date else "mtime")
    event = _fm_date(fm, "date") or file_date
    floor_ = min(mtime_date, cap)
    if declared and declared <= mtime_date and declared <= cap:
        rank = declared
    else:
        rank = floor_
        if not declared:
            source = "mtime"
    return NoteMeta(title, declared, event, rank, source, fm)


# ---- 13.4.3 comments, 13.4.4 wikilinks ------------------------------------------------------
def strip_comments(text: str) -> str:
    out: list[str] = []
    in_fence: Optional[str] = None
    in_comment: Optional[str] = None     # "%%" or "-->"
    for line in text.split("\n"):
        if in_comment is None:
            fm = _FENCE_RE.match(line)
            if in_fence:
                out.append(line)
                if fm and fm.group(1) == in_fence:
                    in_fence = None
                continue
            if fm:
                in_fence = fm.group(1)
                out.append(line)
                continue
        buf, i, in_code = [], 0, False
        while i < len(line):
            if in_comment:
                j = line.find(in_comment, i)
                if j < 0:
                    i = len(line)
                    break
                i = j + len(in_comment)
                in_comment = None
                continue
            ch = line[i]
            if ch == "`":
                in_code = not in_code
                buf.append(ch)
                i += 1
                continue
            if not in_code and line.startswith("%%", i):
                in_comment = "%%"
                i += 2
                continue
            if not in_code and line.startswith("<!--", i):
                in_comment = "-->"
                i += 4
                continue
            buf.append(ch)
            i += 1
        joined = "".join(buf)
        out.append(re.sub(r"(?<=\S) {2,}(?=\S)", " ", joined) if joined != line else joined)
    return "\n".join(out)


def unwrap_links(text: str) -> str:
    text = re.sub(r"!\[\[[^\]]*\]\]", "", text)
    text = re.sub(r"\[\[([^\]|]*)\|([^\]]*)\]\]", r"\2", text)
    return re.sub(r"\[\[([^\]]*)\]\]", r"\1", text)


# ---- 13.4.6 header field cleaning -----------------------------------------------------------
_PIPES = "∣│｜ǀ❘׀"
_SLASHES = "∕⁄／"


def clean_field(text: str) -> str:
    text = clean_controls(text)                                                   # (1)
    out, prev = [], ""
    for ch in text:                                                                # (2)
        if unicodedata.category(ch) in ("Mn", "Me") and not (prev and unicodedata.category(prev).startswith("L")):
            continue
        out.append(ch)
        prev = ch
    text = "".join(out)
    text = "".join("-" if (unicodedata.category(c) == "Pd" or c == "−") else
                   "/" if c in _SLASHES else "|" if c in _PIPES else c for c in text)   # (3)
    text = _ISO_RE.sub(r"\1/\2/\3", text)                                          # (4)
    text = "".join(" " if (c == "|" or unicodedata.category(c) in ("Ps", "Pe") or
                           "BRACKET" in unicodedata.name(c, "")) else c for c in text)   # (5)
    return re.sub(r"\s+", " ", text).strip()


def first_sentence(text: str, max_tokens: int = 25) -> str:
    for para in text.split("\n"):
        p = para.strip()
        if p and not p.startswith(("|", "#", "```", "~~~")):
            s = _SENT_RE.split(p, maxsplit=1)[0]
            words = s.split()
            return " ".join(words[:max_tokens])
    return ""


# ---- 13.4.5 / 13.4.7 sections and size ------------------------------------------------------
def _sections(body: str):
    """Yield (breadcrumb list, section text)."""
    stack: list[tuple[int, str]] = []
    cur: list[str] = []
    in_fence = None
    for line in body.split("\n"):
        fm = _FENCE_RE.match(line)
        if in_fence:
            cur.append(line)
            if fm and fm.group(1) == in_fence:
                in_fence = None
            continue
        if fm:
            in_fence = fm.group(1)
            cur.append(line)
            continue
        h = _HEADING_RE.match(line)
        if h:
            yield [t for _, t in stack], "\n".join(cur)
            level = len(h.group(1))
            stack = [(lv, t) for lv, t in stack if lv < level] + [(level, h.group(2).strip())]
            cur = []
            continue
        cur.append(line)
    yield [t for _, t in stack], "\n".join(cur)


def _blocks(text: str) -> list[tuple[str, str]]:
    """Paragraph blocks; tables (with a following caption line) and fenced code kept whole."""
    lines = text.split("\n")
    blocks, i = [], 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if _FENCE_RE.match(line):
            fence = _FENCE_RE.match(line).group(1)
            j = i + 1
            while j < len(lines) and not (_FENCE_RE.match(lines[j]) and _FENCE_RE.match(lines[j]).group(1) == fence):
                j += 1
            blocks.append(("para", "\n".join(lines[i:j + 1])))
            i = j + 1
            continue
        if line.lstrip().startswith("|"):
            j = i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            if j < len(lines) and lines[j].strip() and not lines[j].lstrip().startswith(("#", "|")):
                j += 1   # caption line stays with the table
            blocks.append(("table", "\n".join(lines[i:j])))
            i = j
            continue
        j = i
        while j < len(lines) and lines[j].strip() and not lines[j].lstrip().startswith("|"):
            j += 1
        blocks.append(("para", "\n".join(lines[i:j])))
        i = j
    return blocks


def _split_table(table: str, budget: int, count) -> list[str]:
    rows = table.split("\n")
    head, rest = rows[:2], rows[2:]
    groups, cur = [], []
    for r in rest:
        if cur and count("\n".join(head + cur + [r])) > budget:
            groups.append("\n".join(head + cur))
            cur = []
        cur.append(r)
    groups.append("\n".join(head + cur))
    return groups


def _hard_split(text: str, budget: int, count) -> list[str]:
    words, parts, cur = text.split(), [], []
    for w in words:
        if cur and count(" ".join(cur + [w])) > budget:
            parts.append(" ".join(cur))
            cur = []
        cur.append(w)
    if cur:
        parts.append(" ".join(cur))
    return parts


def _units(block_kind: str, text: str, budget: int, count) -> list[str]:
    if count(text) <= budget:
        return [text]
    if block_kind == "table":
        return _split_table(text, budget, count)
    out = []
    for sent in _SENT_RE.split(text):
        out.extend([sent] if count(sent) <= budget else _hard_split(sent, budget, count))
    return out


def chunk_note(relpath: str, raw_text: str, mtime_date: str, cap: str,
               count_tokens: Callable[[str], int] = approx_tokens,
               limit: int = MAX_CHUNK_TOKENS, embed_seq: int = 512) -> tuple[NoteMeta, list[Chunk]]:
    limit = min(limit, embed_seq - 32)
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    fm, body = split_frontmatter(text)
    meta = note_meta(relpath, fm, mtime_date, cap)
    body = unwrap_links(strip_comments(body))
    title = clean_field(meta.title)
    chunks: list[Chunk] = []
    event_pending = bool(meta.event_date and meta.event_date != meta.rank_date)
    for crumbs, sec in _sections(body):
        if not sec.strip():
            continue
        crumb = " > ".join(clean_field(c) for c in crumbs)
        lead = clean_field(first_sentence(sec))
        header = f"[{meta.rank_date} | {title}" + (f" | {crumb}" if crumb else "") + (f" | {lead}" if lead else "") + "]"
        if event_pending:
            sec = f"Event date: {meta.event_date}.\n\n" + sec
            event_pending = False
        budget = max(16, limit - count_tokens(header))
        units: list[str] = []
        for kind, blk in _blocks(sec):
            units.extend(_units(kind, blk, budget, count_tokens))
        cur: list[str] = []
        for u in units:
            if cur and count_tokens("\n\n".join(cur + [u])) > budget:
                chunks.append(Chunk(len(chunks), header, "\n\n".join(cur), title, crumb, 0))
                tail = cur[-1].split()
                keep = int(len(tail) * OVERLAP) if not cur[-1].lstrip().startswith("|") else 0
                cur = [" ".join(tail[-keep:])] if keep else []
                if cur and count_tokens("\n\n".join(cur + [u])) > budget:
                    cur = []
            cur.append(u)
        if cur:
            chunks.append(Chunk(len(chunks), header, "\n\n".join(cur), title, crumb, 0))
    if event_pending:   # a note with no body text still records its event date
        header = f"[{meta.rank_date} | {title}]"
        chunks.append(Chunk(0, header, f"Event date: {meta.event_date}.", title, "", 0))
    for c in chunks:
        c.token_count = count_tokens(c.header + "\n" + c.body)
    return meta, chunks


def frontmatter_json(fm: dict) -> str:
    return json.dumps(fm, ensure_ascii=False, sort_keys=True)
