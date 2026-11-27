"""Query pipeline (12.1-12.3, 8.6, 15): transcript in, (display_text, tts_text) out."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from . import budget as B
from .brain.chunker import approx_tokens, header_fields
from .engine.answer import answer
from .engine.client import ChatParams, Engine
from .prompts import (PromptChunk, build_brain, build_general, is_abstention, leaked,
                      prefix_still_abstaining)
from .voice import replies as R
from .voice import router as RT
from .voice.normalize import normalize
from .voice.tts import to_tts

DEADLINE_S = {"quick": 60.0, "deep": 200.0}
GENERAL_FALLBACK = "GENERAL_FALLBACK"
MIN_GENERAL_TOKENS = 60
FALLBACK_RESERVE_DEFAULT_S = 20.0
BUSY_WAIT_S = 5.0


class Busy(Exception):
    """A second interactive request waited 5 s for the chat engine (8.5): 503 BUSY."""
    status, code = 503, "BUSY"


class EngineLoading(Exception):
    """The request needs a chat engine and none is READY (e.g. QUICK stopped for a DEEP swap): 503 LOADING."""
    status, code = 503, "LOADING"


@dataclass
class Profile:
    control_tokens: tuple = ()
    stop: tuple = ()
    supports_enable_thinking: bool = False
    pp: Optional[float] = None        # prompt tokens/s (measured by M9/M9b)
    tg: Optional[float] = None        # generated tokens/s


@dataclass
class Deps:
    engines: dict                                   # "quick"/"deep" -> Engine
    profiles: dict                                  # "quick"/"deep" -> Profile
    engine_ready: Callable[[str], bool]
    vault_ready: Callable[[], bool]
    search: Callable[[str, str], list]              # (search text, floor name) -> [Hit]
    partial_transient: Callable[[], bool]           # includes the 13.3.7 rescan
    still_reading_n: Callable[[], int]
    command: Callable[[str], tuple]                 # local command -> (display, tts); runs it
    count_tokens: Callable[[str], int] = approx_tokens
    deep_failed: Callable[[], bool] = lambda: False
    # Called once routing knows a DEEP request needs a chat engine (8.3). Returns the monotonic time
    # the engine that will answer became READY (the deadline counts from max(start, that)), or None.
    prepare_deep: Callable[[], Optional[float]] = lambda: None
    pronunciation: str = "Glad-eon"
    clock: Callable[[], float] = time.monotonic


@dataclass
class Reply:
    route: str
    mode: str
    display_text: str
    tts_text: str
    warnings: list = field(default_factory=list)
    chunk_ids: list = field(default_factory=list)


class Pipeline:
    def __init__(self, deps: Deps):
        self.d = deps
        self.followup = RT.FollowUp()
        self._hits: dict = {}          # this request's hits (chunk id -> Hit)
        self._fu_hits: dict = {}       # hits behind followup.chunk_ids, kept only as long as the follow-up
        self.lock = threading.Lock()   # one conversation; the proxy serializes chat anyway

    # ---- helpers ---------------------------------------------------------------------------
    def wipe(self) -> None:
        """Drop all conversation state and any note text held in memory (lock, 12.3.5)."""
        self._reset_followup()
        self._hits = {}

    def _reset_followup(self) -> None:
        self.followup.wipe()
        self._fu_hits = {}

    def _fixed(self, route: str, mode: str, pair, warnings=(), keep_route: Optional[str] = None) -> Reply:
        if keep_route == RT.FINANCE_DEFERRED:
            self.followup.route, self.followup.turns = RT.FINANCE_DEFERRED, []
            self.followup.last_reply = self.d.clock()
            self._fu_hits = {}
        else:
            self.wipe()
        return Reply(route, mode, pair[0], pair[1], list(warnings))

    def _search_ids(self, text: str, floor_name: str) -> list:
        hits = self.d.search(text, floor_name)
        for h in hits:
            self._hits[h.chunk_id] = h
        return [h.chunk_id for h in hits]

    def _engine_for(self, mode: str, warnings: list) -> tuple[Optional[str], str]:
        """Returns (engine name or None, display prefix)."""
        if mode == "deep":
            if not self.d.deep_failed() and self.d.engine_ready("deep"):
                return "deep", ""
            if self.d.engine_ready("quick"):
                warnings.append("DEEP_UNAVAILABLE")
                return "quick", R.DEEP_UNAVAILABLE_PREFIX
            return None, ""
        if self.d.engine_ready("quick"):
            return "quick", ""
        if self.d.engine_ready("deep"):
            return "deep", ""
        return None, ""

    def _params(self, eng: str, mode: str, brain: bool, max_tokens: Optional[int] = None) -> ChatParams:
        p = self.d.profiles[eng]
        return ChatParams("DEEP" if mode == "deep" else "QUICK", brain=brain, max_tokens=max_tokens,
                          stop=p.stop, supports_enable_thinking=p.supports_enable_thinking)

    def _reserve_s(self, eng: str, general_prompt_tokens: int) -> float:
        p = self.d.profiles[eng]
        if not p.pp or not p.tg:
            return FALLBACK_RESERVE_DEFAULT_S
        return general_prompt_tokens / p.pp + MIN_GENERAL_TOKENS / p.tg

    # ---- main entry ------------------------------------------------------------------------
    def handle(self, transcript: str, body_mode: str = "auto", start: Optional[float] = None) -> Reply:
        """One conversation at a time; a second request waits at most 5 s, then BUSY (8.5)."""
        start = self.d.clock() if start is None else start
        if not self.lock.acquire(timeout=BUSY_WAIT_S):
            raise Busy()
        try:
            return self._handle(transcript, body_mode, start)
        finally:
            self._hits = {}                       # note text from this request is not kept
            self.lock.release()

    def _handle(self, transcript: str, body_mode: str, start: float) -> Reply:
        warnings: list = []
        self._hits = dict(self._fu_hits)          # per request; follow-up hits carried explicitly
        n = normalize(transcript, body_mode)
        mode = n.mode
        if n.empty:
            return self._fixed("EMPTY_WAKE", mode, R.EMPTY_WAKE)
        text, cut = B.truncate_transcript(n.text, self.d.count_tokens)
        search_text = n.search if not cut else B.truncate_transcript(n.search, self.d.count_tokens)[0]
        if cut:
            warnings.append("TRANSCRIPT_TRUNCATED")
        deadline = start + DEADLINE_S[mode]
        fu = self.followup if self.followup.active(self.d.clock()) else None
        if fu is None and self.followup.route is not None:
            self.wipe()
        rr = RT.route(text, search_text, self.d.vault_ready(), self._search_ids,
                      self.d.partial_transient, fu, self.d.clock())
        r = rr.route
        if r == RT.LOCAL_COMMAND:
            return self._fixed(r, mode, self.d.command(rr.command), warnings)
        if r == RT.LOCK_HINT:
            return self._fixed(r, mode, R.LOCK_HINT, warnings)
        if r == RT.FINANCE_DEFERRED:
            return self._fixed(r, mode, R.FINANCE_DEFERRED, warnings, keep_route=RT.FINANCE_DEFERRED)
        if r == RT.VAULT_LOCKED:
            return self._fixed(r, mode, R.VAULT_LOCKED, warnings)
        if r == RT.NOT_IN_NOTES:
            return self._fixed(r, mode, R.NOT_IN_NOTES, warnings)
        if r == RT.STILL_READING:
            return self._fixed(r, mode, R.still_reading(self.d.still_reading_n()), warnings)

        if mode == "deep":
            # the DEEP load can take minutes: let other requests (fixed replies) through meanwhile
            self.lock.release()
            try:
                ready_at = self.d.prepare_deep()
            finally:
                self.lock.acquire()
            if ready_at is not None:
                base = max(start, ready_at)
                # answered by DEEP: 200 s; fell back to QUICK: its own 60 s from QUICK READY (8.3)
                deadline = base + (DEADLINE_S["deep"] if self.d.engine_ready("deep") else DEADLINE_S["quick"])
        eng, prefix = self._engine_for(mode, warnings)
        if eng is None:
            raise EngineLoading()
        sticky = fu is not None and rr.route == fu.route
        turns = list(fu.turns) if sticky else []
        question = text
        if r == RT.BRAIN_RAG:
            rep = self._brain(rr, text, turns, mode, eng, deadline, warnings, fu if sticky else None)
        else:
            rep = self._general(question, turns, mode, eng, deadline, warnings, r)
        if prefix and rep.route not in (RT.NOT_IN_NOTES,) and rep.display_text not in (R.OUT_OF_TIME[0], R.ENGINE_EMPTY[0]):
            rep.display_text = f"{prefix} {rep.display_text}"
            rep.tts_text = to_tts(rep.display_text, self.d.pronunciation)
        return rep

    # ---- GENERAL ---------------------------------------------------------------------------
    def _general(self, question: str, turns, mode: str, eng: str, deadline: float, warnings: list,
                 route: str, max_tokens: Optional[int] = None) -> Reply:
        prof = self.d.profiles[eng]
        bp = build_general(question, turns, mode, prof.control_tokens)
        a = answer(self.d.engines[eng], bp.messages, self._params(eng, mode, False, max_tokens), deadline,
                   prof.control_tokens)
        return self._finish(a, route, mode, question, warnings, [], store_route=RT.GENERAL_CHAT)

    # ---- BRAIN -----------------------------------------------------------------------------
    def _brain(self, rr, text: str, turns, mode: str, eng: str, deadline: float, warnings: list, fu) -> Reply:
        prof = self.d.profiles[eng]
        ids = list(rr.hits)
        hits = [self._hits[i] for i in ids if i in self._hits]
        ct = self.d.count_tokens
        pcs = [PromptChunk(h.rank_date, h.title, _crumb(h.header), h.body) for h in hits]
        sys_tokens = ct(build_brain("", [], (), mode).messages[0]["content"])
        fitted = B.fit(mode, sys_tokens, turns, pcs,
                       chunk_tokens=lambda c: ct(f"[{c.rank_date} | {c.title} | {c.breadcrumb}]\n{c.body}"),
                       turn_tokens=lambda t: ct(t[0]) + ct(t[1]), question_tokens=ct(text))
        if not fitted.chunks:
            if rr.fallback_eligible:
                return self._general(text, [], mode, eng, deadline, warnings, GENERAL_FALLBACK)
            return self._fixed(RT.NOT_IN_NOTES, mode, R.NOT_IN_NOTES, warnings)
        used_ids = [h.chunk_id for h, c in zip(hits, pcs) if c in fitted.chunks]
        bp = build_brain(text, fitted.chunks, fitted.turns, mode, prof.control_tokens)
        gq = (fu.question + " " + text) if fu is not None else text
        on_text = None
        if rr.fallback_eligible:
            g_tokens = ct(build_general(gq, (), mode).messages[0]["content"]) + ct(gq)
            reserve = self._reserve_s(eng, g_tokens)
            if self.d.clock() >= deadline - reserve:
                return self._general(gq, [], mode, eng, deadline, warnings, GENERAL_FALLBACK)

            def on_text(acc: str) -> bool:
                return not (prefix_still_abstaining(acc, prof.control_tokens)
                            and self.d.clock() >= deadline - reserve)
        a = answer(self.d.engines[eng], bp.messages, self._params(eng, mode, True), deadline,
                   prof.control_tokens, tag=bp.tag, on_text=on_text)
        if rr.fallback_eligible and (is_abstention(a.text, prof.control_tokens)
                                     or (a.text and prefix_still_abstaining(a.text, prof.control_tokens)
                                         and self.d.clock() >= deadline - reserve)):
            return self._general(gq, [], mode, eng, deadline, warnings, GENERAL_FALLBACK)
        if a.text and leaked(a.text, bp.nonce):
            return self._fixed(RT.NOT_IN_NOTES, mode, R.NOT_IN_NOTES, warnings + ["RAG_LEAK_BLOCKED"])
        if a.timed_out and a.partial and leaked(a.partial, bp.nonce):
            return self._fixed(RT.NOT_IN_NOTES, mode, R.NOT_IN_NOTES, warnings + ["RAG_LEAK_BLOCKED"])
        rep = self._finish(a, RT.BRAIN_RAG, mode, text, warnings, used_ids, store_route=RT.BRAIN_RAG)
        if rep.route == RT.BRAIN_RAG:
            self.followup.chunk_ids = used_ids
            self.followup.fallback_eligible = rr.fallback_eligible
            self.followup.question = gq
            self._fu_hits = {i: self._hits[i] for i in used_ids if i in self._hits}
        return rep

    # ---- shared ending ---------------------------------------------------------------------
    def _finish(self, a, route: str, mode: str, question: str, warnings: list, chunk_ids, store_route: str) -> Reply:
        if a.timed_out:
            part = B.last_sentences(a.partial)
            if not part:
                return self._fixed("OUT_OF_TIME", mode, R.OUT_OF_TIME, warnings)
            warnings.append("ANSWER_TRUNCATED")
            text = part
        elif not a.text.strip():
            return self._fixed("ENGINE_EMPTY", mode, R.ENGINE_EMPTY, warnings + ([a.error] if a.error else []))
        else:
            text = a.text.strip()
        fu = self.followup
        if fu.route != store_route:
            self._reset_followup()          # keeps this request's hits
            fu.route = store_route
        fu.turns = (fu.turns + [(question, text)])[-2:]
        fu.question = question
        fu.last_reply = self.d.clock()
        if store_route != RT.BRAIN_RAG:
            self._fu_hits = {}
        return Reply(route, mode, text, to_tts(text, self.d.pronunciation), warnings, list(chunk_ids))


def _crumb(header: str) -> str:
    f = header_fields(header)
    return f[2] if len(f) == 4 else ""
