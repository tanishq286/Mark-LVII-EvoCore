"""Milestone 11 tests: Productivity & Agents (PPTX, Scraper/Shopping, Website, Image, Office/PDF/Convert, Email, WhatsApp)."""

from __future__ import annotations

from pathlib import Path
import threading


def test_deck_generator_and_research_export(tmp_path: Path):
    from pptx import Presentation
    from core.tasks.research import export_to_pptx

    md = "# Executive Summary\n- Latency reduced 42%\n- RTX local routing stable\n## Next Steps\n- Ship Milestone 11\n"
    out_file = tmp_path / "briefing.pptx"
    msg = export_to_pptx(md, title="M11 Status Deck", out_path=str(out_file))
    assert "Created PowerPoint deck" in msg
    assert out_file.is_file()
    prs = Presentation(str(out_file))
    assert len(prs.slides) == 3  # Title + 2 content sections


def test_web_scraper_and_shopping_compare():
    from plugins.web_scraper import extract_structured, shopping_compare

    sample_html = """
    <html><head><title>Spec Page</title></head><body>
      <h1>RTX Workstation</h1>
      <div class="spec">12GB VRAM</div>
      <table><tr><th>Metric</th><th>Value</th></tr><tr><td>Score</td><td>98</td></tr></table>
    </body></html>
    """
    ext = extract_structured(sample_html, css=".spec", xpath="//h1/text()", extract_tables=True)
    assert ext["title"] == "Spec Page"
    assert ext["css_matches"] == ["12GB VRAM"]
    assert ext["xpath_matches"] == ["RTX Workstation"]
    assert ext["tables"][0][1] == ["Score", "98"]

    cmp = shopping_compare(
        "Sony WH-1000XM5",
        store_snapshots=[
            {"store": "Flipkart", "html": "<h2>Sony XM5</h2><span class='p'>₹26,990</span>", "title_css": "h2", "price_css": ".p"},
            {"store": "Amazon.in", "html": "<h2>Sony XM5</h2><span class='p'>₹24,999</span>", "title_css": "h2", "price_css": ".p"},
            {"store": "Croma", "html": "<h2>Sony XM5</h2><span class='p'>₹28,490</span>", "title_css": "h2", "price_css": ".p"},
        ],
    )
    assert "<<<UNTRUSTED>>>" in cmp
    assert "Best Price: Amazon.in at INR 24,999.00." in cmp


def test_website_builder_and_image_generator(tmp_path: Path):
    from PIL import Image
    from plugins import image_generator, website_builder

    site_dir = tmp_path / "demo_site"
    res = website_builder.run({
        "action": "build",
        "title": "Astra <AI>",
        "prompt": "Autonomous fleet console",
        "template": "saas",
        "out_dir": str(site_dir),
        "preview": False,
    })
    assert "index.html" in res
    html_text = (site_dir / "index.html").read_text(encoding="utf-8")
    assert "Astra &lt;AI&gt;" in html_text
    assert "<canvas id=\"hero-canvas\">" in html_text
    assert "grid-template-columns" in html_text

    png = tmp_path / "art.png"
    img_res = image_generator.run({
        "action": "generate",
        "prompt": "Cybernetic Reactor Core",
        "backend": "local_poster",
        "width": 640,
        "height": 400,
        "out_path": str(png),
    })
    assert "local_poster" in img_res and png.is_file()
    with Image.open(png) as im:
        assert im.size == (640, 400)


def test_office_suite_excel_pdf_tables_and_batch_convert(tmp_path: Path):
    from docx import Document
    from PIL import Image
    from actions.file_processor import file_processor
    from plugins import office_suite

    wb_path = tmp_path / "metrics.xlsx"
    assert "Created Excel" in office_suite.run({
        "action": "excel_create",
        "path": str(wb_path),
        "headers": ["Module", "Tier", "Score"],
        "rows": [["VoiceLock", "LOCKED", 99], ["WebScraper", "SAFE", 95]],
    })
    assert "Appended" in office_suite.run({"action": "excel_append", "path": str(wb_path), "rows": [["Avatar", "SAFE", 97]]})
    assert "Updated" in office_suite.run({"action": "excel_update", "path": str(wb_path), "cell": "C2", "value": 100})
    filt = office_suite.run({"action": "excel_filter", "path": str(wb_path), "column": "Tier", "equals": "SAFE"})
    assert "WebScraper" in filt and "Avatar" in filt and "VoiceLock" not in filt
    assert "Formatted workbook" in office_suite.run({"action": "excel_format", "path": str(wb_path)})
    assert "100" in office_suite.run({"action": "excel_read", "path": str(wb_path)})

    # Batch convert docx -> pdf, xlsx -> csv, png -> webp
    doc_path = tmp_path / "brief.docx"
    d = Document()
    d.add_paragraph("Mark-LVII Batch Document Export")
    d.save(str(doc_path))

    png_path = tmp_path / "icon.png"
    Image.new("RGB", (64, 64), (0, 212, 255)).save(str(png_path))

    conv_pdf = office_suite.run({"action": "batch_convert", "paths": [str(doc_path)], "target_format": "pdf"})
    assert "1/1" in conv_pdf and (tmp_path / "brief.pdf").is_file()
    conv_csv = office_suite.run({"action": "batch_convert", "paths": [str(wb_path)], "target_format": "csv"})
    assert "1/1" in conv_csv and (tmp_path / "metrics.csv").is_file()
    conv_webp = office_suite.run({"action": "batch_convert", "paths": [str(png_path)], "target_format": "webp"})
    assert "1/1" in conv_webp and (tmp_path / "icon.webp").is_file()

    # PDF table extraction hook via file_processor & office_suite using PIL table image / monkeypatched pdfplumber
    import pdfplumber
    class _FakePage:
        def extract_tables(self):
            return [[["ColA", "ColB"], ["x", "1"]]]
    class _FakePdf:
        pages = [_FakePage()]
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
    old_open = pdfplumber.open
    pdfplumber.open = lambda p: _FakePdf()
    try:
        out_tbl = file_processor({"file_path": str(tmp_path / "brief.pdf"), "action": "extract_tables"})
        assert "Exported 1 table(s) to CSV" in out_tbl
        csv_f = tmp_path / "brief_table_1_p1.csv"
        assert csv_f.is_file() and "ColA,ColB" in csv_f.read_text(encoding="utf-8")
    finally:
        pdfplumber.open = old_open


def test_email_automator_and_whatsapp_media_call_gates(tmp_path: Path, monkeypatch):
    import time as _time
    from core import confirm, env_loader
    from actions import send_message as sm
    from plugins import email_automator

    att = tmp_path / "note.txt"
    att.write_text("Quarterly check", encoding="utf-8")

    monkeypatch.setenv("EMAIL_ADDRESS", "jarvis@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "app-pw-demo")
    monkeypatch.setattr(env_loader, "_DOTENV_PATH", tmp_path / "none.env")

    draft = email_automator.run({"action": "draft", "to": "alice@example.com", "subject": "Update", "body": "Ready", "attachments": [str(att)]})
    assert "note.txt" in draft and "Ready" in draft

    sent_msgs = []
    class _FakeSMTP:
        def __init__(self, *a, **kw):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def starttls(self):
            pass
        def login(self, u, p):
            pass
        def send_message(self, msg):
            sent_msgs.append(msg)

    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    orig_cbs = (confirm._show_cb, confirm._hide_cb, confirm._log_cb)
    logs = []
    confirm.resolve(False)
    try:
        seen_gate_titles = []
        confirm.bind(lambda title, d: seen_gate_titles.append(title), lambda: None, lambda m: logs.append(m))

        s_pending = email_automator.run({
            "action": "send", "to": "alice@example.com", "subject": "Deck", "body": "See attached", "attachments": [str(att)],
        })
        assert "[CONFIRMATION_PENDING]" in s_pending
        confirm.resolve(True)
        deadline = _time.monotonic() + 2.0
        while _time.monotonic() < deadline and not sent_msgs:
            _time.sleep(0.03)
        assert len(sent_msgs) == 1

        # WhatsApp media & call gates
        monkeypatch.setattr(sm, "_PYAUTOGUI", True)
        called = []
        monkeypatch.setattr(sm, "_send_whatsapp_media", lambda r, m, c="": (called.append(("media", r)), f"status=done media={r}")[1])
        monkeypatch.setattr(sm, "_whatsapp_call", lambda r, ct="voice": (called.append(("call", ct)), f"status=unverified call={ct}")[1])

        r_media = sm.send_message({"receiver": "Alice", "platform": "WhatsApp", "media_path": str(att), "message_text": "Here you go"})
        assert "[CONFIRMATION_PENDING]" in r_media
        confirm.resolve(True)
        deadline = _time.monotonic() + 2.0
        while _time.monotonic() < deadline and ("media", "Alice") not in called:
            _time.sleep(0.03)
        assert ("media", "Alice") in called

        r_call = sm.send_message({"receiver": "Alice", "platform": "WhatsApp", "call_type": "video"})
        assert "[CONFIRMATION_PENDING]" in r_call
        confirm.resolve(True)
        deadline = _time.monotonic() + 2.0
        while _time.monotonic() < deadline and ("call", "video") not in called:
            _time.sleep(0.03)
        assert ("call", "video") in called
        assert len(seen_gate_titles) >= 3

        bad_media = sm.send_message({"receiver": "Alice", "platform": "WhatsApp", "media_path": str(tmp_path / "missing.png")})
        assert "status=failed" in bad_media
    finally:
        confirm.resolve(False)
        confirm.bind(*orig_cbs)
