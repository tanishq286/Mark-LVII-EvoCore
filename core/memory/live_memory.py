"""Bridge between the Gemini Live loop (main.py) and cross-session memory.

Everything that touches disk or a model runs on ONE background worker thread
(ordered, so turns are stored in sequence) - never on the asyncio loop that
feeds Gemini Live audio. main.py only calls the non-blocking methods below.

Lifecycle
---------
* startup  -> `start()`: new store session + summarise sessions a crash left
              unsummarised (background, Ollama first).
* each turn -> `record(role, text)`: append-only insert + rule extraction.
* sleep    -> `on_sleep()`: summarise + extract the conversation, open a new
              store session, and flag that the next wake needs a memory primer.
* wake     -> `wake_primer()`: the bootstrap block to inject (once per sleep).
* reconnect / new Live session -> `bootstrap_text()` for the system prompt.
* shutdown -> `on_shutdown()`: marks the session ended (fast; the next start
              summarises it with a model, so exit can never hang on an LLM).
"""
from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from core.memory import conversation_store as store
from core.memory import memory_extractor as extractor


class LiveMemoryBridge:
    def __init__(self, *, db_path: Path | None = None, use_llm: bool = True,
                 allow_cloud: bool = True) -> None:
        self._db = db_path
        self._use_llm = use_llm
        self._allow_cloud = allow_cloud
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-memory")
        self._lock = threading.Lock()
        self._sid = ""
        self._turns_in_session = 0
        self._needs_primer = False
        self.last_error = ""

    # ── helpers ──────────────────────────────────────────────────────────────
    def _submit(self, fn, *a: Any, **kw: Any) -> Future:
        def _safe():
            try:
                return fn(*a, **kw)
            except Exception as exc:  # memory must never take the voice loop down
                self.last_error = f"{type(exc).__name__}: {exc}"
                print(f"[Memory] background step failed: {self.last_error}")
                return None
        return self._pool.submit(_safe)

    @property
    def session_id(self) -> str:
        with self._lock:
            return self._sid

    def _ensure_session(self) -> str:
        with self._lock:
            if not self._sid:
                self._sid = store.start_session("live", db_path=self._db)
                self._turns_in_session = 0
            return self._sid

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self, *, recover: bool = True) -> Future:
        sid = self._ensure_session()
        if not recover:
            return self._submit(lambda: None)
        return self._submit(extractor.recover_unsummarized, exclude_session=sid,
                            use_llm=self._use_llm, allow_cloud=self._allow_cloud, db_path=self._db)

    def record(self, role: str, text: str, *, source: str = "voice") -> Future | None:
        body = " ".join(str(text or "").split())
        if not body:
            return None
        sid = self._ensure_session()
        with self._lock:
            self._turns_in_session += 1

        def _work():
            store.append_turn(role, body, session_id=sid, source=source, db_path=self._db)
            if str(role).lower() == "user":
                extractor.process_turn(body, session_id=sid, db_path=self._db)
        return self._submit(_work)

    def on_sleep(self, reason: str = "sleep") -> Future | None:
        with self._lock:
            sid, n = self._sid, self._turns_in_session
            if not sid or n == 0:
                return None
            self._sid, self._turns_in_session = "", 0
            self._needs_primer = True

        def _work():
            store.end_session(sid, reason, db_path=self._db)
            return extractor.process_session_end(sid, use_llm=self._use_llm,
                                                 allow_cloud=self._allow_cloud, db_path=self._db)
        return self._submit(_work)

    def on_shutdown(self, reason: str = "shutdown", timeout: float = 5.0) -> None:
        with self._lock:
            sid, self._sid = self._sid, ""
        if sid:
            fut = self._submit(store.end_session, sid, reason, db_path=self._db)
            try:
                fut.result(timeout=timeout)
            except Exception:
                pass
        self._pool.shutdown(wait=False, cancel_futures=False)

    def flush(self, timeout: float = 10.0) -> None:
        """Block until every queued write has landed (tests / shutdown)."""
        try:
            self._submit(lambda: None).result(timeout=timeout)
        except Exception:
            pass

    # ── context injection ────────────────────────────────────────────────────
    def bootstrap_text(self, *, query_hint: str = "") -> str:
        """Memory block for a new Live session's system prompt. Includes the
        current process's earlier turns too, so a server-side reconnect (1008
        inactivity / GoAway) no longer wipes the conversation context."""
        try:
            return store.build_bootstrap_context(query_hint=query_hint, db_path=self._db)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return ""

    def wake_primer(self) -> str:
        """Bootstrap text to inject on wake - only once after a sleep."""
        with self._lock:
            if not self._needs_primer:
                return ""
            self._needs_primer = False
        text = self.bootstrap_text()
        if not text:
            return ""
        return ("[MEMORY REFRESH after sleep - context only, do not reply to this message]\n" + text)
