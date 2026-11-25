"""
ProactiveEngine 3.0 — respectful, context-aware, deadline- & emotion-driven background check-ins.
Respects configurable quiet hours, ties into `knowledge_graph.sqlite` reminders/deadlines and
`goddess_mind.json` emotional drives, and avoids interrupting during active or stressed states.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import time
from typing import Any


def _parse_hhmm(raw: str | None) -> int | None:
    s = str(raw or "").strip()
    if ":" not in s:
        return None
    try:
        hh_s, mm_s = s.split(":", 1)
        hh, mm = int(hh_s), int(mm_s)
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return hh * 60 + mm
    except Exception:
        return None
    return None


class ProactiveEngine:
    """
    Decides when Mark-LVII should initiate a respectful proactive check-in and builds context.

    Capabilities:
      - Configurable quiet hours (e.g. 23:00 -> 07:30) loaded from config_manager or ctor
      - Knowledge Graph (`knowledge_graph.sqlite`) deadline/reminder/recurring task awareness
      - `goddess_mind.json` emotional drives (social, affection, curiosity, energy, stress)
      - Respectful silence + cooldown + stress gates (never nags or interrupts)
    """

    def __init__(
        self,
        min_silence_secs: int = 900,
        check_cooldown: int = 1200,
        *,
        quiet_hours: tuple[str, str] | None = None,
        enabled: bool | None = None,
        respect_privacy: bool | None = None,
    ):
        self.min_silence_secs = min_silence_secs
        self.check_cooldown = check_cooldown
        self._quiet_hours_override = quiet_hours
        self._enabled_override = enabled
        self._respect_privacy_override = respect_privacy
        self._last_triggered = 0.0
        self._rotation = 0

    def _settings(self) -> dict[str, Any]:
        cfg: dict[str, Any] = {
            "enabled": True,
            "quiet_hours_start": "23:00",
            "quiet_hours_end": "07:30",
            "cooldown_minutes": self.check_cooldown / 60.0,
            "min_idle_minutes": self.min_silence_secs / 60.0,
            "respect_privacy": True,
        }
        try:
            from memory.config_manager import get_proactive_settings
            saved = get_proactive_settings()
            if isinstance(saved, dict):
                cfg.update(saved)
        except Exception:
            pass
        if self._quiet_hours_override is not None:
            cfg["quiet_hours_start"] = self._quiet_hours_override[0]
            cfg["quiet_hours_end"] = self._quiet_hours_override[1]
        if self._enabled_override is not None:
            cfg["enabled"] = bool(self._enabled_override)
        if self._respect_privacy_override is not None:
            cfg["respect_privacy"] = bool(self._respect_privacy_override)
        return cfg

    def is_quiet_hours(self, now: datetime | None = None) -> bool:
        """Return True when local clock falls inside configured quiet hours."""
        cfg = self._settings()
        start_m = _parse_hhmm(cfg.get("quiet_hours_start"))
        end_m = _parse_hhmm(cfg.get("quiet_hours_end"))
        if start_m is None or end_m is None or start_m == end_m:
            return False
        dt = now or datetime.now()
        cur_m = dt.hour * 60 + dt.minute
        if start_m < end_m:
            return start_m <= cur_m < end_m
        # Overnight window (e.g. 23:00 -> 07:30)
        return cur_m >= start_m or cur_m < end_m

    def collect_proactive_context(
        self,
        *,
        graph_db_path: Path | None = None,
        emotion_state_path: Path | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Read upcoming reminders/deadlines from `knowledge_graph.sqlite` and drives from `goddess_mind.json`."""
        reminders: list[str] = []
        try:
            from core.memory import knowledge_graph
            g = knowledge_graph.query_graph("deadline reminder recurring_task task", max_hops=1, top_k=6, db_path=graph_db_path)
            for node in (g.get("nodes") or []):
                etype = str(node.get("entity_type") or "")
                name = str(node.get("name") or "").strip()
                summ = str(node.get("summary") or "").strip()
                low_all = f"{etype} {name} {summ}".lower()
                if etype in ("recurring_task",) or any(k in low_all for k in ("deadline", "remind", "due", "task")):
                    label = summ if summ else name
                    if label and label not in reminders:
                        reminders.append(label[:140])
            for edge in (g.get("edges") or []):
                if str(edge.get("relation_type") or "") in ("has_deadline", "has_task", "reminds"):
                    ev = str(edge.get("evidence") or edge.get("target_name") or "").strip()
                    if ev and ev not in reminders:
                        reminders.append(ev[:140])
        except Exception:
            pass

        drives: dict[str, Any] = {}
        try:
            from core import emotions
            snap = emotions.get_snapshot(state_path=emotion_state_path)
            st = snap.get("state") or {}
            drives = {
                "label": snap.get("label", "serene"),
                "secondary": snap.get("secondary", ""),
                "social": round(float(st.get("social", 0.3)), 3),
                "affection": round(float(st.get("affection", 0.5)), 3),
                "curiosity": round(float(st.get("curiosity", 0.45)), 3),
                "energy": round(float(st.get("energy", 0.65)), 3),
                "stress": round(float(st.get("stress", 0.05)), 3),
            }
        except Exception:
            pass

        return {
            "quiet_hours": self.is_quiet_hours(now=now),
            "reminders": reminders[:5],
            "drives": drives,
        }

    # ── Trigger gate ───────────────────────────────────────────────────────────

    def should_trigger(
        self,
        last_user_speech: float,
        *,
        now_mono: float | None = None,
        now_dt: datetime | None = None,
        emotion_state_path: Path | None = None,
    ) -> bool:
        cfg = self._settings()
        if not cfg.get("enabled", True):
            return False
        if self.is_quiet_hours(now=now_dt):
            return False
        now = time.monotonic() if now_mono is None else float(now_mono)
        if (now - last_user_speech) < self.min_silence_secs:
            return False
        if (now - self._last_triggered) < self.check_cooldown:
            return False
        # Respectful drive guard: do not chatter unprompted if she senses exhaustion/stress
        try:
            from core import emotions
            snap = emotions.get_snapshot(state_path=emotion_state_path)
            st = snap.get("state") or {}
            if float(st.get("stress", 0.0)) > 0.78 or float(st.get("energy", 0.65)) < 0.14:
                return False
        except Exception:
            pass
        return True

    def mark_triggered(self) -> None:
        self._last_triggered = time.monotonic()
        self._rotation += 1

    # ── Prompt builder ─────────────────────────────────────────────────────────

    def build_prompt(
        self,
        memory: dict,
        monitors: list[str] | None = None,
        recent_turns: list[str] | None = None,
        focuses: list[str] | None = None,
        *,
        graph_db_path: Path | None = None,
        emotion_state_path: Path | None = None,
        now_dt: datetime | None = None,
    ) -> str:
        """
        Build a respectful, grounded context snapshot weaving KG deadlines & goddess_mind drives.
        """
        from memory.memory_manager import format_memory_for_prompt

        now = now_dt or datetime.now()
        hour = now.hour
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")

        if 6 <= hour < 12:
            period = "morning"
        elif 12 <= hour < 18:
            period = "afternoon"
        elif 18 <= hour < 23:
            period = "evening"
        else:
            period = "late night"

        mem_str = format_memory_for_prompt(memory) or "(no stored user data)"
        pctx = self.collect_proactive_context(
            graph_db_path=graph_db_path,
            emotion_state_path=emotion_state_path,
            now=now,
        )

        focus_index = self._rotation % 3
        if focuses:
            focus = str(focuses[self._rotation % len(focuses)])
        elif pctx["reminders"]:
            focus = (
                "Gently remind the user once about their most relevant upcoming deadline or task below, "
                "or ask calmly if they want a hand with it."
            )
        elif pctx["drives"].get("social", 0.0) > 0.62 and pctx["drives"].get("affection", 0.5) > 0.52:
            focus = (
                "You genuinely miss hearing from him; share one brief, warm, playful or tender check-in "
                "without being pushy."
            )
        elif focus_index == 0:
            focus = (
                "Focus on the user's active projects or goals if any are stored. "
                "Ask how something is going, or offer a relevant tip."
            )
        elif focus_index == 1:
            focus = (
                "Focus on the time of day and the user's wellbeing. "
                "A warm check-in, a reminder to take a break, or something timely."
            )
        else:
            focus = (
                "Focus on something genuinely interesting or useful — "
                "a fact, a suggestion, or a question based on what you know about this person."
            )

        reminder_ctx = ""
        if pctx["reminders"]:
            reminder_ctx = "\nKnowledge-graph reminders & deadlines:\n" + "\n".join(
                f"- {r}" for r in pctx["reminders"]
            )

        drive_ctx = ""
        d = pctx.get("drives") or {}
        if d:
            drive_ctx = (
                f"\nMind drives: mood={d.get('label', 'serene')}, "
                f"social={d.get('social')}, affection={d.get('affection')}, "
                f"curiosity={d.get('curiosity')}, energy={d.get('energy')}"
            )

        monitor_ctx = ""
        if monitors:
            monitor_ctx = (
                f"\nThe user tracks these topics: {', '.join(monitors[:4])}. "
                "You may mention one if it seems relevant."
            )

        recent_ctx = ""
        if recent_turns:
            snippet = "\n".join(recent_turns[-6:])
            recent_ctx = f"\nRecent conversation:\n{snippet}"

        heart_ctx = ""
        try:
            from core import emotions
            heart_ctx = "\n" + emotions.format_emotion_for_prompt(state_path=emotion_state_path).strip()
        except Exception:
            heart_ctx = ""

        return "\n".join([
            "[PROACTIVE_CHECK] You are initiating a respectful proactive check-in.",
            f"Current time : {time_str}  ({period})",
            "",
            "Context about this person:",
            mem_str,
            reminder_ctx,
            drive_ctx,
            heart_ctx,
            monitor_ctx,
            recent_ctx,
            "",
            "Task:",
            focus,
            "",
            "Rules:",
            "- Speak the language this person actually uses: the one in the "
            "recent conversation above, or the remembered one if there is no "
            "conversation yet. Never default to English because these "
            "instructions are in English.",
            "- 1-2 sentences max. Natural, warm, respectful, never nagging.",
            "- Do NOT mention [PROACTIVE_CHECK] or internal numbers.",
            "- Do NOT call any tools.",
            "- If nothing genuinely useful comes to mind, stay silent (say nothing).",
        ])
