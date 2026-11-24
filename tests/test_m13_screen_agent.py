"""Tests for Milestone 13 Closed-Loop Autonomous Screen Agent."""
from __future__ import annotations

from pathlib import Path
import threading
import time

from core import confirm, screenact, screenview
from core.evolution import habit_compiler, planner, safety_guard, screen_agent, telemetry
from plugins import autonomous_brain, autonomous_screen_agent


class _FakePlayer:
    def __init__(self) -> None:
        self.logs: list[str] = []
        self.jobs: list[dict] = []
        self.contents: list[tuple[str, str]] = []
        self.said: list[str] = []

    def write_log(self, msg: str) -> None:
        self.logs.append(str(msg))

    def show_code_job(self, snap: dict) -> None:
        self.jobs.append(dict(snap))

    def show_content(self, title: str, text: str) -> None:
        self.contents.append((title, text))

    def request_say(self, text: str) -> None:
        self.said.append(str(text))


def _make_view(
    *,
    app: str = "desktop",
    title: str = "Desktop",
    source: str = "uia",
    degraded: bool = False,
    elements: list[screenview.Element] | None = None,
    windows: list[tuple[str, str]] | None = None,
    digest: str = "d0",
) -> screenview.View:
    els = elements if elements is not None else []
    win_list = windows if windows is not None else ([(app, title)] if title else [])
    return screenview.View(
        os_name="windows",
        source=source,
        degraded=degraded,
        reason="thin UIA" if degraded else "",
        app=app,
        title=title,
        pid=4242,
        bounds=(0, 0, 1920, 1080),
        window_rect=(0, 0, 1920, 1080),
        elements=els,
        windows=win_list,
        digest=digest,
        stable_digest=digest,
        scan_ms=12.0,
    )


def test_hybrid_grounding_uia_and_vision_fallback(tmp_path: Path):
    db = telemetry.init_db(tmp_path / "telemetry.sqlite")
    try:
        # 1. Normal UIA view resolves directly without invoking vision fallback
        search_btn = screenview.Element(
            1, screenview.ROLE_BUTTON, "Search", "", 600, 120, 140, 36, enabled=True
        )
        rich_view = _make_view(app="chrome", title="New Tab - Google Chrome", elements=[search_btn], digest="d1")
        rich_obs = screen_agent.observe_screen(
            look_fn=lambda: rich_view,
            capture_fn=lambda: (b"frame-1", "image/jpeg"),
        )
        assert len(rich_obs.frame_hash) == 16

        vision_calls: list[str] = []
        agent_uia = screen_agent.AutonomousScreenAgent(
            db_path=db,
            vision_fn=lambda g, t, o: (vision_calls.append(t), None)[1],
        )
        resolved_uia, err = agent_uia._resolve_target("Open Search", {"target": "Search"}, rich_obs)
        assert err == ""
        assert isinstance(resolved_uia, screenview.Element)
        assert resolved_uia.center == (670, 138)
        assert vision_calls == []

        # 2. Degraded/thin browser UIA escalates cleanly to Vision/OCR coordinates + frame hash
        degraded_view = _make_view(
            app="chrome",
            title="YouTube - Google Chrome",
            source="none",
            degraded=True,
            elements=[],
            digest="d2",
        )
        deg_obs = screen_agent.observe_screen(
            look_fn=lambda: degraded_view,
            capture_fn=lambda: b"youtube-screenshot-bytes",
        )
        assert len(deg_obs.frame_hash) == 16

        agent_vis = screen_agent.AutonomousScreenAgent(
            db_path=db,
            vision_fn=lambda _goal, hint, _obs: {
                "summary": "YouTube search bar visible at top",
                "target_point": {"x": 850, "y": 110, "label": hint, "confidence": 0.94},
                "elements": [{"label": "Search", "role": "input", "x": 850, "y": 110}],
            },
        )
        resolved_vis, err2 = agent_vis._resolve_target(
            "Search lo-fi beats",
            {"action": "click", "target": "Search"},
            deg_obs,
        )
        assert err2 == ""
        assert isinstance(resolved_vis, screenact.Point)
        assert resolved_vis.origin == "vision"
        assert (resolved_vis.x, resolved_vis.y) == (850, 110)
    finally:
        screen_agent.clear_global_abort()


def test_closed_loop_observe_verify_act_and_hud_streaming(tmp_path: Path):
    db = telemetry.init_db(tmp_path / "telemetry.sqlite")
    player = _FakePlayer()

    views = [
        _make_view(app="explorer", title="Desktop", digest="v0", windows=[("explorer", "Desktop")]),
        _make_view(
            app="chrome",
            title="New Tab - Google Chrome",
            elements=[screenview.Element(1, screenview.ROLE_EDIT, "Search or type URL", "", 400, 70, 600, 32)],
            windows=[("chrome", "New Tab - Google Chrome")],
            digest="v1",
        ),
        _make_view(
            app="chrome",
            title="YouTube - Google Chrome",
            elements=[screenview.Element(1, screenview.ROLE_EDIT, "Search", "", 520, 110, 420, 36)],
            windows=[("chrome", "YouTube - Google Chrome")],
            digest="v2",
        ),
        _make_view(
            app="chrome",
            title="lo-fi beats - YouTube - Google Chrome",
            elements=[
                screenview.Element(1, screenview.ROLE_EDIT, "Search", "lo-fi beats", 520, 110, 420, 36),
                screenview.Element(2, screenview.ROLE_TEXT, "lofi hip hop radio", "", 520, 260, 400, 40),
            ],
            windows=[("chrome", "lo-fi beats - YouTube - Google Chrome")],
            digest="v3",
        ),
    ]
    frames = [b"f0", b"f1", b"f2", b"f3"]
    state_idx = {"i": 0}
    decisions_script = [
        {
            "status": "act",
            "hud_summary": "Launching Chrome browser",
            "action": "open_app",
            "target": "chrome",
            "expected_app": "chrome",
            "expected_window_title": "Google Chrome",
        },
        {
            "status": "act",
            "hud_summary": "Verified Chrome opened -> Clicking YouTube search bar",
            "action": "click",
            "target": "Search",
            "expected_window_title": "YouTube",
        },
        {
            "status": "act",
            "hud_summary": "Verified YouTube loaded -> Searching for lo-fi beats",
            "action": "type_text",
            "target": "Search",
            "text": "lo-fi beats",
            "expected_text": "lo-fi beats",
        },
        {
            "status": "done",
            "thought": "YouTube results for lo-fi beats are live on screen.",
        },
    ]

    def look_fn():
        return views[min(state_idx["i"], len(views) - 1)]

    def capture_fn():
        return frames[min(state_idx["i"], len(frames) - 1)], "image/jpeg"

    def decide_fn(ctx):
        step = ctx["step"] - 1
        return decisions_script[step]

    def act_exec_fn(_step, _obs):
        state_idx["i"] += 1
        return screenact.Act(ok=True, action=str(_step.get("action")), target=str(_step.get("target") or ""), origin="element")

    try:
        agent = screen_agent.AutonomousScreenAgent(
            max_steps=12,
            settle_timeout_s=0.1,
            poll_interval_s=0.005,
            db_path=db,
            look_fn=look_fn,
            capture_fn=capture_fn,
            decide_fn=decide_fn,
            act_exec_fn=act_exec_fn,
            sleep_fn=lambda _s: None,
        )
        report = agent.run("Open Chrome, go to YouTube, and search for lo-fi beats", player=player)
        assert report["status"] == "completed"
        assert report["steps_executed"] == 3
        assert report["steps_verified"] == 3
        assert report["steps_recovered"] == 0
        assert any("Verified Chrome opened -> Clicking YouTube search bar" in s for s in report["hud_steps"])
        assert any("ScreenAgent" in log for log in player.logs)
        assert len(player.jobs) >= 4
    finally:
        screen_agent.clear_global_abort()


def test_post_action_settle_polling_waits_for_change(tmp_path: Path):
    db = telemetry.init_db(tmp_path / "telemetry.sqlite")
    v_before = _make_view(app="desktop", title="Desktop", digest="same_d", windows=[("explorer", "Desktop")])
    v_after = _make_view(app="notepad", title="Untitled - Notepad", digest="new_d", windows=[("notepad", "Untitled - Notepad")])

    look_seq = [v_before, v_before, v_before, v_after]
    frame_seq = [b"same", b"same", b"same", b"changed"]
    poll_cursor = {"i": 0}

    def look_fn():
        idx = min(poll_cursor["i"], len(look_seq) - 1)
        val = look_seq[idx]
        return val

    def capture_fn():
        idx = min(poll_cursor["i"], len(frame_seq) - 1)
        val = frame_seq[idx]
        poll_cursor["i"] += 1
        return val, "image/jpeg"

    steps = [
        {"status": "act", "action": "open_app", "target": "notepad", "expected_app": "notepad"},
        {"status": "done", "thought": "Notepad verified open."},
    ]

    try:
        agent = screen_agent.AutonomousScreenAgent(
            max_steps=6,
            settle_timeout_s=0.5,
            poll_interval_s=0.002,
            db_path=db,
            look_fn=look_fn,
            capture_fn=capture_fn,
            decide_fn=lambda ctx: steps[ctx["step"] - 1],
            act_exec_fn=lambda _s, _o: screenact.Act(ok=True, action="open_app", target="notepad"),
            sleep_fn=lambda _s: None,
        )
        rep = agent.run("Open Notepad", player=_FakePlayer())
        assert rep["status"] == "completed"
        assert rep["steps_verified"] == 1
        assert rep["steps_recovered"] == 0
        # 1 initial observe + 3 settle polls (unchanged x2, then changed)
        assert poll_cursor["i"] >= 4
    finally:
        screen_agent.clear_global_abort()


def test_stuck_state_recovery_and_choice_banner_ambiguity(tmp_path: Path):
    db = telemetry.init_db(tmp_path / "telemetry.sqlite")
    orig_choice = (confirm._show_choice_cb, confirm._hide_choice_cb)
    shown_choices: list[tuple[str, list[str]]] = []

    try:
        # Part A: Screen unchanged on primary UIA click -> automatic recovery via Vision coordinate succeeds!
        btn_search = screenview.Element(1, screenview.ROLE_BUTTON, "Search", "", 100, 100, 90, 30)
        obs_initial = _make_view(app="chrome", title="YouTube", elements=[btn_search], digest="static")
        obs_after_rec = _make_view(app="chrome", title="YouTube - Search Ready", elements=[btn_search], digest="moved")

        exec_origins: list[str] = []
        mode = {"recovered": False}

        def look_a():
            return obs_after_rec if mode["recovered"] else obs_initial

        def cap_a():
            return (b"img_new" if mode["recovered"] else b"img_old"), "image/jpeg"

        def act_a(step, _obs):
            resolved = step.get("_resolved_target")
            origin = getattr(resolved, "origin", "element")
            exec_origins.append(origin)
            if origin == "vision":
                mode["recovered"] = True
            return screenact.Act(ok=True, action="click", target="Search", origin=origin)

        decisions_a = [
            {"status": "act", "action": "click", "target": "Search", "expect_change": True},
            {"status": "done", "thought": "Recovered via Vision click."},
        ]

        agent_rec = screen_agent.AutonomousScreenAgent(
            max_steps=6,
            settle_timeout_s=0.02,
            poll_interval_s=0.005,
            db_path=db,
            look_fn=look_a,
            capture_fn=cap_a,
            vision_fn=lambda _g, hint, _o: {"target_point": {"x": 540, "y": 125, "label": hint}},
            decide_fn=lambda ctx: decisions_a[ctx["step"] - 1],
            act_exec_fn=act_a,
            sleep_fn=lambda _s: None,
        )
        rep_a = agent_rec.run("Click Search on YouTube", player=_FakePlayer())
        assert rep_a["status"] == "completed"
        assert rep_a["steps_recovered"] == 1
        assert exec_origins == ["element", "vision"]

        # Part B: Still stuck after recovery + multiple ambiguous targets -> ChoiceBanner triggered
        confirm.bind_choice(
            lambda title, _detail, opts: shown_choices.append((title, opts)),
            lambda: None,
        )
        cand1 = screenview.Element(1, screenview.ROLE_BUTTON, "Search Videos", "", 100, 100, 120, 30)
        cand2 = screenview.Element(2, screenview.ROLE_BUTTON, "Search Shorts", "", 250, 100, 120, 30)
        obs_stuck = _make_view(app="chrome", title="YouTube", elements=[cand1, cand2], digest="stuck")

        agent_amb = screen_agent.AutonomousScreenAgent(
            max_steps=6,
            settle_timeout_s=0.02,
            poll_interval_s=0.005,
            db_path=db,
            look_fn=lambda: obs_stuck,
            capture_fn=lambda: (b"same_stuck", "image/jpeg"),
            vision_fn=lambda _g, hint, _o: {"target_point": {"x": 200, "y": 115, "label": hint}},
            decide_fn=lambda _ctx: {"status": "act", "action": "click", "target": "Search", "expect_change": True},
            act_exec_fn=lambda _s, _o: screenact.Act(ok=True, action="click", target="Search"),
            sleep_fn=lambda _s: None,
        )
        rep_b = agent_amb.run("Click Search", player=_FakePlayer())
        assert rep_b["status"] == "awaiting_choice"
        assert len(shown_choices) == 1
        assert len(rep_b["choice_options"]) == 2
        assert confirm.pending_choice_info() is not None
        confirm.resolve_choice(None, sync=True)
    finally:
        confirm.bind_choice(*orig_choice)
        screen_agent.clear_global_abort()


def test_irreversible_action_gate_blocks_until_confirm_and_cancels_cleanly(tmp_path: Path):
    db = telemetry.init_db(tmp_path / "telemetry.sqlite")
    orig_cbs = (confirm._show_cb, confirm._hide_cb, confirm._log_cb)

    try:
        obs_mail = _make_view(
            app="chrome",
            title="Compose Mail - Gmail - Google Chrome",
            elements=[screenview.Element(1, screenview.ROLE_BUTTON, "Send", "", 80, 620, 90, 36)],
            digest="gmail",
        )
        obs_notepad = _make_view(app="notepad", title="notes.txt - Notepad", digest="np")

        # 1. Classifier checks (true positives + zero false positives on safe search/save)
        assert screen_agent.classify_irreversible_action({"action": "click", "target": "Send"}, obs_mail)["tier"] == safety_guard.CONFIRM
        assert screen_agent.classify_irreversible_action({"action": "click", "target": "Delete"}, obs_notepad)["tier"] == safety_guard.CONFIRM
        assert screen_agent.classify_irreversible_action({"action": "click", "target": "Pay Now"}, obs_mail)["tier"] == safety_guard.LOCKED
        assert screen_agent.classify_irreversible_action({"action": "click", "target": "Shut down"}, obs_notepad)["tier"] == safety_guard.LOCKED
        assert screen_agent.classify_irreversible_action({"action": "click", "target": "Install"}, obs_notepad)["tier"] == safety_guard.CONFIRM
        assert screen_agent.classify_irreversible_action({"action": "click", "target": "Save"}, obs_notepad) is None
        assert screen_agent.classify_irreversible_action({"action": "type_text", "target": "Search", "text": "lo-fi beats"}, obs_notepad) is None

        # 2. Headless (no HUD bound): irreversible action must NOT execute
        confirm.bind(None, None, None)
        executed: list[dict] = []
        agent_headless = screen_agent.AutonomousScreenAgent(
            db_path=db,
            look_fn=lambda: obs_mail,
            capture_fn=lambda: (b"m1", "image/jpeg"),
            decide_fn=lambda _c: {"status": "act", "action": "click", "target": "Send"},
            act_exec_fn=lambda s, _o: (executed.append(s), screenact.Act(ok=True, action="click"))[1],
        )
        rep_headless = agent_headless.run("Send email")
        assert rep_headless["status"] == "awaiting_confirmation"
        assert executed == []

        # 3. HUD bound + user clicks CANCEL -> halts without executing
        shown_banners: list[tuple[str, str]] = []
        confirm.bind(
            lambda t, d: shown_banners.append((t, d)),
            lambda: None,
            lambda _m: None,
        )

        def _auto_resolve(accepted: bool):
            deadline = time.monotonic() + 2.0
            while not confirm.pending_title() and time.monotonic() < deadline:
                time.sleep(0.01)
            confirm.resolve(accepted)

        t_cancel = threading.Thread(target=_auto_resolve, args=(False,), daemon=True)
        t_cancel.start()
        agent_cancel = screen_agent.AutonomousScreenAgent(
            db_path=db,
            wait_for_confirm_s=2.0,
            look_fn=lambda: obs_mail,
            capture_fn=lambda: (b"m2", "image/jpeg"),
            decide_fn=lambda _c: {"status": "act", "action": "click", "target": "Send"},
            act_exec_fn=lambda s, _o: (executed.append(s), screenact.Act(ok=True, action="click"))[1],
        )
        rep_cancel = agent_cancel.run("Send email")
        t_cancel.join(timeout=1.0)
        assert rep_cancel["status"] == "cancelled_by_user"
        assert executed == []

        # 4. HUD bound + user clicks CONFIRM -> proceeds and completes
        sent = {"done": False}
        obs_sent = _make_view(app="chrome", title="Sent - Gmail - Google Chrome", digest="sent")

        t_ok = threading.Thread(target=_auto_resolve, args=(True,), daemon=True)
        t_ok.start()
        steps_ok = [
            {"status": "act", "action": "click", "target": "Send", "expected_window_title": "Sent"},
            {"status": "done", "thought": "Email sent after user confirm."},
        ]
        agent_ok = screen_agent.AutonomousScreenAgent(
            db_path=db,
            settle_timeout_s=0.05,
            poll_interval_s=0.005,
            wait_for_confirm_s=2.0,
            look_fn=lambda: obs_sent if sent["done"] else obs_mail,
            capture_fn=lambda: (b"sent" if sent["done"] else b"compose", "image/jpeg"),
            decide_fn=lambda ctx: steps_ok[ctx["step"] - 1],
            act_exec_fn=lambda s, _o: (executed.append(s), sent.__setitem__("done", True), screenact.Act(ok=True, action="click"))[2],
            sleep_fn=lambda _s: None,
        )
        rep_ok = agent_ok.run("Send email", player=_FakePlayer())
        t_ok.join(timeout=1.0)
        assert rep_ok["status"] == "completed"
        assert len(executed) == 1
        assert len(shown_banners) >= 2
    finally:
        confirm.bind(*orig_cbs)
        screen_agent.clear_global_abort()


def test_instant_abort_kill_switches(tmp_path: Path):
    db = telemetry.init_db(tmp_path / "telemetry.sqlite")
    obs = _make_view(
        app="chrome",
        title="Chrome",
        elements=[screenview.Element(1, screenview.ROLE_BUTTON, "Anything", "", 50, 50, 100, 30)],
        digest="c1",
    )

    try:
        # (a) ESC key abort
        esc_ticks = {"n": 0}
        agent_esc = screen_agent.AutonomousScreenAgent(
            db_path=db,
            look_fn=lambda: obs,
            capture_fn=lambda: (b"c", "image/jpeg"),
            esc_pressed_fn=lambda: (esc_ticks.__setitem__("n", esc_ticks["n"] + 1), esc_ticks["n"] >= 2)[1],
            decide_fn=lambda _c: {"status": "act", "action": "wait", "expect_change": False},
            act_exec_fn=lambda _s, _o: screenact.Act(ok=True, action="wait"),
            settle_timeout_s=0.01,
            sleep_fn=lambda _s: None,
        )
        rep_esc = agent_esc.run("Long task")
        assert rep_esc["status"] == "aborted"
        assert rep_esc["abort_reason"] == "esc_key"

        # (b) pyautogui corner FailSafeException abort
        class FailSafeException(RuntimeError):
            pass

        def raise_failsafe(_s, _o):
            raise FailSafeException("PyAutoGUI fail-safe triggered from moving mouse to a corner")

        agent_fs = screen_agent.AutonomousScreenAgent(
            db_path=db,
            look_fn=lambda: obs,
            capture_fn=lambda: (b"c", "image/jpeg"),
            decide_fn=lambda _c: {"status": "act", "action": "click", "target": "Anything"},
            act_exec_fn=raise_failsafe,
            esc_pressed_fn=lambda: False,
        )
        rep_fs = agent_fs.run("Click target")
        assert rep_fs["status"] == "aborted"
        assert rep_fs["abort_reason"] == "mouse_failsafe"

        # (c) Voice phrase + autonomous_screen_agent(action='stop')
        assert screen_agent.matches_voice_abort("Jarvis stop") is True
        assert screen_agent.matches_voice_abort("hello how is weather") is False

        calls = {"n": 0}

        def abort_on_step_1(_step, _obs):
            calls["n"] += 1
            out = autonomous_screen_agent.run({"action": "stop"})
            assert "abort requested" in out.lower()
            return screenact.Act(ok=True, action="open_app", target="chrome")

        agent_voice = screen_agent.AutonomousScreenAgent(
            db_path=db,
            look_fn=lambda: obs,
            capture_fn=lambda: (b"c", "image/jpeg"),
            decide_fn=lambda _c: {"status": "act", "action": "open_app", "target": "chrome", "expect_change": False},
            act_exec_fn=abort_on_step_1,
            esc_pressed_fn=lambda: False,
            settle_timeout_s=0.05,
        )
        rep_voice = agent_voice.run("Open Chrome and search")
        assert rep_voice["status"] == "aborted"
        assert calls["n"] == 1
    finally:
        screen_agent.clear_global_abort()


def test_step_telemetry_feeds_habit_compiler(tmp_path: Path):
    db = telemetry.init_db(tmp_path / "telemetry.sqlite")
    habits_file = tmp_path / "habits.json"

    try:
        for _run_idx in range(3):
            seq_idx = {"i": 0}
            obs_seq = [
                _make_view(app="desktop", title="Desktop", digest="s0"),
                _make_view(app="chrome", title="Chrome", digest="s1"),
                _make_view(app="chrome", title="YouTube", digest="s2"),
                _make_view(app="chrome", title="YouTube Results", digest="s3"),
            ]
            steps = [
                {"status": "act", "action": "open_app", "target": "chrome"},
                {"status": "act", "action": "click", "target": "Search"},
                {"status": "act", "action": "type_text", "target": "Search", "text": "lofi"},
                {"status": "done", "thought": "Done"},
            ]

            agent = screen_agent.AutonomousScreenAgent(
                max_steps=6,
                settle_timeout_s=0.01,
                poll_interval_s=0.002,
                db_path=db,
                look_fn=lambda: obs_seq[min(seq_idx["i"], len(obs_seq) - 1)],
                capture_fn=lambda: (f"f{seq_idx['i']}".encode(), "image/jpeg"),
                vision_fn=lambda _g, h, _o: {"target_point": {"x": 500, "y": 100, "label": h}},
                decide_fn=lambda ctx: steps[ctx["step"] - 1],
                act_exec_fn=lambda _s, _o: (seq_idx.__setitem__("i", seq_idx["i"] + 1), screenact.Act(ok=True, action="act"))[1],
                esc_pressed_fn=lambda: False,
                sleep_fn=lambda _s: None,
            )
            rep = agent.run("Open Chrome and search lofi")
            assert rep["status"] == "completed"

        events = telemetry.recent_tool_sequence(limit=20, db_path=db)
        assert len(events) == 9
        assert all(ev["tool_name"] == "autonomous_screen_agent" for ev in events)

        candidates = habit_compiler.detect_habit_candidates(
            db_path=db,
            habits_path=habits_file,
            min_support=3,
        )
        assert len(candidates) >= 1
        assert candidates[0]["support_count"] >= 3
        assert candidates[0]["length"] == 3
    finally:
        screen_agent.clear_global_abort()


def test_planner_and_autonomous_brain_routing_and_plugin_contract(monkeypatch):
    # 1. Planner GUI goal classification
    assert planner.is_gui_screen_goal("Open Chrome, go to YouTube, and search for lo-fi beats") is True
    assert planner.is_gui_screen_goal("Open Notepad, write my notes, and save it") is True
    assert planner.is_gui_screen_goal("Summarize my git status") is False

    # 2. Plugin status smoke contract (zero side effects)
    st = autonomous_screen_agent.run({}, player=None)
    assert isinstance(st, str) and "screen agent" in st.lower()

    # 3. VerifiedPlanner.plan exposes requires_screen_loop flag
    vp = planner.VerifiedPlanner()
    plan = vp.plan(
        "Open Chrome, go to YouTube, and search for lo-fi beats",
        "- autonomous_screen_agent",
        llm_json_fn=lambda _p: {"strategy": "Screen loop", "steps": []},
    )
    assert plan["requires_screen_loop"] is True

    # 4. autonomous_brain routes GUI missions to run_screen_goal + cancel stops both
    routed_goals: list[str] = []

    def fake_run_screen_goal(goal, **_kw):
        routed_goals.append(goal)
        return {
            "status": "completed",
            "goal": goal,
            "steps_executed": 2,
            "steps_verified": 2,
            "steps_recovered": 0,
            "steps_failed": 0,
            "hud_steps": ["Step 1/12: Verified Chrome opened", "Step 2/12: Verified complete"],
            "summary": "Goal verified.",
        }

    monkeypatch.setattr(screen_agent, "run_screen_goal", fake_run_screen_goal)
    player = _FakePlayer()
    autonomous_brain._lock.acquire()
    autonomous_brain._execute_mission("Open Chrome, go to YouTube, and search for lo-fi beats", 6, player)
    assert routed_goals == ["Open Chrome, go to YouTube, and search for lo-fi beats"]

    msg_cancel = autonomous_brain.run({"action": "cancel"})
    assert "screen agent" in msg_cancel.lower()
    screen_agent.clear_global_abort()
