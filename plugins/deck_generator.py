"""plugins/deck_generator.py — PowerPoint (.pptx) presentation & research deck generator."""

from __future__ import annotations

from pathlib import Path
import re
import time

_BASE_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_OUT_DIR = _BASE_DIR / "reports"

PLUGIN = {
    "name": "deck_generator",
    "brief": "Generate styled .pptx PowerPoint slide decks from topics, slides, or Markdown reports",
    "description": (
        "Builds multi-slide widescreen PowerPoint (.pptx) presentations via python-pptx with "
        "configurable dark HUD or light corporate themes, bullet layouts, speaker notes, and "
        "direct conversion from research report Markdown. Actions: status, create, from_markdown."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Action: status | create | from_markdown (default: status)",
            },
            "title": {
                "type": "STRING",
                "description": "Presentation title.",
            },
            "subtitle": {
                "type": "STRING",
                "description": "Subtitle on the title slide.",
            },
            "slides": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "List of slide dicts: [{'title': str, 'bullets': list[str], 'notes': str}].",
            },
            "markdown": {
                "type": "STRING",
                "description": "Markdown report text to split by headings into slides.",
            },
            "out_path": {
                "type": "STRING",
                "description": "Output .pptx file path (defaults into reports/).",
            },
            "theme": {
                "type": "STRING",
                "description": "Theme: 'dark' (default HUD dark) or 'light'.",
            },
        },
        "required": [],
    },
}


def _markdown_to_slides(md_text: str) -> list[dict]:
    slides: list[dict] = []
    cur_title = "Executive Summary"
    cur_bullets: list[str] = []

    for raw_line in (md_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m_head = re.match(r"^#{1,3}\s+(.+)$", line)
        if m_head:
            if cur_bullets:
                slides.append({"title": cur_title, "bullets": cur_bullets[:7], "notes": ""})
                cur_bullets = []
            cur_title = m_head.group(1).strip()
            continue
        cleaned = re.sub(r"^[-*•]\s+", "", line)
        cleaned = re.sub(r"^\d+\.\s+", "", cleaned)
        if cleaned:
            cur_bullets.append(cleaned[:220])
            if len(cur_bullets) >= 6:
                slides.append({"title": cur_title, "bullets": cur_bullets, "notes": ""})
                cur_bullets = []
                cur_title = f"{cur_title} (cont.)"

    if cur_bullets:
        slides.append({"title": cur_title, "bullets": cur_bullets[:7], "notes": ""})
    return slides or [{"title": "Overview", "bullets": ["No structured headings provided."], "notes": ""}]


def build_presentation(
    *,
    title: str = "JARVIS Briefing",
    subtitle: str = "",
    slides: list[dict] | None = None,
    out_path: str | Path | None = None,
    theme: str = "dark",
) -> dict:
    """Create a `.pptx` deck on disk and return metadata `{path, slide_count}`."""
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Inches, Pt

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    dark = str(theme or "dark").strip().lower() != "light"
    bg_rgb = RGBColor(7, 17, 26) if dark else RGBColor(248, 250, 252)
    fg_rgb = RGBColor(224, 248, 255) if dark else RGBColor(15, 23, 42)
    acc_rgb = RGBColor(0, 212, 255) if dark else RGBColor(15, 64, 96)
    mut_rgb = RGBColor(135, 190, 210) if dark else RGBColor(71, 85, 105)

    def _paint_bg(slide) -> None:
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = bg_rgb

    # Title Slide
    s0 = prs.slides.add_slide(prs.slide_layouts[6])
    _paint_bg(s0)
    tb = s0.shapes.add_textbox(Inches(1.0), Inches(2.2), Inches(11.3), Inches(1.8))
    tf = tb.text_frame
    tf.word_wrap = True
    p0 = tf.paragraphs[0]
    p0.text = str(title or "JARVIS Presentation")
    p0.font.size = Pt(40)
    p0.font.bold = True
    p0.font.color.rgb = acc_rgb

    sub_box = s0.shapes.add_textbox(Inches(1.0), Inches(4.1), Inches(11.0), Inches(1.2))
    stf = sub_box.text_frame
    stf.word_wrap = True
    sp = stf.paragraphs[0]
    sp.text = str(subtitle or time.strftime("Generated %Y-%m-%d"))
    sp.font.size = Pt(20)
    sp.font.color.rgb = mut_rgb

    for item in (slides or []):
        stitle = str(item.get("title") or "Section")
        bullets = [str(b) for b in (item.get("bullets") or []) if str(b).strip()]
        notes = str(item.get("notes") or "").strip()

        sl = prs.slides.add_slide(prs.slide_layouts[6])
        _paint_bg(sl)

        hdr = sl.shapes.add_textbox(Inches(0.8), Inches(0.55), Inches(11.7), Inches(0.9))
        htf = hdr.text_frame
        htf.word_wrap = True
        hp = htf.paragraphs[0]
        hp.text = stitle
        hp.font.size = Pt(28)
        hp.font.bold = True
        hp.font.color.rgb = acc_rgb

        body = sl.shapes.add_textbox(Inches(0.9), Inches(1.55), Inches(11.5), Inches(5.4))
        btf = body.text_frame
        btf.word_wrap = True
        if not bullets:
            bullets = ["Summary points pending."]
        for idx, b in enumerate(bullets):
            bp = btf.paragraphs[0] if idx == 0 else btf.add_paragraph()
            bp.text = f"• {b}"
            bp.font.size = Pt(18)
            bp.font.color.rgb = fg_rgb
            bp.space_after = Pt(10)

        if notes:
            sl.notes_slide.notes_text_frame.text = notes

    if out_path:
        target = Path(out_path)
    else:
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", title.lower()).strip("_")[:48] or "deck"
        target = _DEFAULT_OUT_DIR / f"{safe_name}_{time.strftime('%Y%m%d_%H%M%S')}.pptx"
    target.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(target))
    return {"path": str(target), "slide_count": len(prs.slides)}


def create_deck_from_markdown(
    markdown: str,
    *,
    title: str = "JARVIS Research Briefing",
    out_path: str | Path | None = None,
    theme: str = "dark",
) -> str:
    """Convert Markdown text into a `.pptx` deck and return a summary string."""
    slides = _markdown_to_slides(markdown)
    info = build_presentation(title=title, slides=slides, out_path=out_path, theme=theme)
    return f"Created PowerPoint deck ({info['slide_count']} slides): {info['path']}"


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Plugin entrypoint for slide-deck generation."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "status").strip().lower()

    if action == "status":
        try:
            import pptx  # noqa: F401
            return f"Deck Generator ready (python-pptx available, output_dir={_DEFAULT_OUT_DIR})."
        except ImportError:
            return "Deck Generator unavailable: python-pptx is not installed."

    title = str(params.get("title") or "JARVIS Briefing").strip()
    subtitle = str(params.get("subtitle") or "").strip()
    out_path = params.get("out_path")
    theme = str(params.get("theme") or "dark").strip().lower()

    if action == "from_markdown" or params.get("markdown"):
        md = str(params.get("markdown") or "").strip()
        if not md:
            return "Provide markdown='...' to convert a report into slides."
        return create_deck_from_markdown(md, title=title, out_path=out_path, theme=theme)

    if action == "create":
        slides = params.get("slides") or []
        if not isinstance(slides, list) or not slides:
            return "Provide slides=[{'title': ..., 'bullets': [...]}] or markdown='...'."
        info = build_presentation(
            title=title,
            subtitle=subtitle,
            slides=slides,
            out_path=out_path,
            theme=theme,
        )
        return f"Created PowerPoint deck ({info['slide_count']} slides): {info['path']}"

    return f"Unknown deck_generator action '{action}'. Use: status, create, from_markdown."
