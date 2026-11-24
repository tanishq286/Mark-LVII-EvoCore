"""plugins/website_builder.py — Single-prompt responsive HTML5/CSS/JS/Canvas website builder."""

from __future__ import annotations

import html
from pathlib import Path
import re
import time
import webbrowser

_BASE_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_SITES_DIR = _BASE_DIR / "generated_sites"

PLUGIN = {
    "name": "website_builder",
    "brief": "Single-prompt responsive HTML5/CSS/JS/Canvas website generator with browser preview",
    "description": (
        "Generates standalone responsive multi-section websites (HTML5 semantic layout, CSS Grid, "
        "modern dark glass/Tailwind utility aesthetics, vanilla JS interactivity, and animated HTML5 Canvas) "
        "from a natural-language prompt and opens optional browser preview. Actions: status, build, templates."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Action: status | build | templates (default: status)",
            },
            "prompt": {
                "type": "STRING",
                "description": "Website description or business brief.",
            },
            "title": {
                "type": "STRING",
                "description": "Site / brand title.",
            },
            "template": {
                "type": "STRING",
                "description": "Layout style: saas | portfolio | landing | dashboard (default: saas).",
            },
            "out_dir": {
                "type": "STRING",
                "description": "Destination folder where index.html will be saved.",
            },
            "preview": {
                "type": "BOOLEAN",
                "description": "Open the built website in default web browser (default: False).",
            },
        },
        "required": [],
    },
}

_TEMPLATES = {
    "saas": ("Next-Gen Cloud Intelligence", "Deploy faster with autonomous telemetry and verifiable workflows.", "#00d4ff", "#07111a"),
    "portfolio": ("Creative Engineering Studio", "Designing high-precision systems, interactive graphics, and AI tools.", "#38bdf8", "#090d16"),
    "landing": ("Precision Product Launch", "Built for clarity, sub-second response, and effortless daily control.", "#22c55e", "#06110e"),
    "dashboard": ("Live Operations Console", "Real-time metrics, fleet status, and deterministic alert streams.", "#f59e0b", "#0d1117"),
}


def synthesize_website_html(
    *,
    prompt: str,
    title: str = "Nova Studio",
    template: str = "saas",
) -> str:
    """Build a standalone responsive HTML5 + CSS Grid + JS + Canvas page."""
    tpl = template if template in _TEMPLATES else "saas"
    default_sub, default_desc, accent, bg = _TEMPLATES[tpl]
    safe_title = html.escape(title.strip() or "Nova Studio")
    safe_prompt = html.escape(prompt.strip() or default_desc)
    safe_sub = html.escape(default_sub)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{safe_title}</title>
  <style>
    :root {{
      --bg: {bg};
      --panel: rgba(13, 25, 38, 0.78);
      --accent: {accent};
      --text: #e6f7ff;
      --muted: #8fb4c9;
      --border: rgba(255, 255, 255, 0.12);
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
      background: radial-gradient(circle at top right, #0e2438 0%, var(--bg) 58%);
      color: var(--text);
      line-height: 1.6;
      min-height: 100vh;
      overflow-x: hidden;
    }}
    #hero-canvas {{
      position: fixed;
      inset: 0;
      z-index: 0;
      pointer-events: none;
      opacity: 0.45;
    }}
    .wrap {{ position: relative; z-index: 1; max-width: 1120px; margin: 0 auto; padding: 28px 24px 64px; }}
    header {{
      display: flex; align-items: center; justify-content: space-between;
      padding: 14px 20px; border: 1px solid var(--border);
      background: var(--panel); backdrop-filter: blur(12px); border-radius: 14px;
    }}
    .brand {{ font-weight: 700; letter-spacing: 0.08em; color: var(--accent); }}
    nav a {{ color: var(--muted); text-decoration: none; margin-left: 20px; font-size: 0.95rem; }}
    nav a:hover {{ color: var(--text); }}
    .hero {{ padding: 72px 0 48px; max-width: 760px; }}
    .badge {{
      display: inline-block; padding: 5px 12px; border-radius: 999px;
      border: 1px solid var(--accent); color: var(--accent); font-size: 0.8rem; margin-bottom: 18px;
    }}
    h1 {{ font-size: clamp(2.2rem, 5vw, 3.6rem); line-height: 1.12; margin-bottom: 16px; }}
    .lead {{ font-size: 1.12rem; color: var(--muted); margin-bottom: 28px; }}
    .cta-row {{ display: flex; gap: 14px; flex-wrap: wrap; }}
    .btn {{
      padding: 12px 24px; border-radius: 10px; font-weight: 600; cursor: pointer;
      border: 1px solid var(--accent); background: var(--accent); color: #041017; transition: transform .15s;
    }}
    .btn.secondary {{ background: transparent; color: var(--text); }}
    .btn:hover {{ transform: translateY(-2px); }}
    .grid {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
      gap: 20px; margin-top: 24px;
    }}
    .card {{
      background: var(--panel); border: 1px solid var(--border);
      border-radius: 14px; padding: 24px; backdrop-filter: blur(10px);
    }}
    .card h3 {{ color: var(--accent); margin-bottom: 8px; font-size: 1.1rem; }}
    .card p {{ color: var(--muted); font-size: 0.95rem; }}
    footer {{ margin-top: 64px; color: var(--muted); font-size: 0.88rem; border-top: 1px solid var(--border); padding-top: 20px; }}
  </style>
</head>
<body>
  <canvas id="hero-canvas"></canvas>
  <div class="wrap">
    <header>
      <div class="brand">{safe_title}</div>
      <nav>
        <a href="#features">Capabilities</a>
        <a href="#architecture">Architecture</a>
        <a href="#contact">Launch</a>
      </nav>
    </header>
    <main>
      <section class="hero">
        <span class="badge">{tpl.upper()} BLUEPRINT</span>
        <h1>{safe_sub}</h1>
        <p class="lead">{safe_prompt}</p>
        <div class="cta-row">
          <button class="btn" id="primary-cta">Get Started</button>
          <button class="btn secondary" id="status-btn">System Telemetry</button>
        </div>
        <p id="cta-feedback" class="lead" style="margin-top:14px;font-size:0.92rem;"></p>
      </section>
      <section id="features" class="grid">
        <article class="card">
          <h3>Adaptive Core</h3>
          <p>Responsive CSS Grid system tuned automatically for desktop, tablet, and mobile viewports.</p>
        </article>
        <article class="card">
          <h3>Canvas Field</h3>
          <p>Hardware-accelerated 60fps HTML5 particle field running locally with zero heavy frameworks.</p>
        </article>
        <article class="card" id="architecture">
          <h3>Interactive Controls</h3>
          <p>Zero-dependency DOM event bindings with accessible contrast ratios and crisp hierarchy.</p>
        </article>
      </section>
      <footer id="contact">Built with Mark-LVII Website Builder · {safe_title}</footer>
    </main>
  </div>
  <script>
    const canvas = document.getElementById('hero-canvas');
    const ctx = canvas.getContext('2d');
    const dots = Array.from({{length: 36}}, () => ({{
      x: Math.random(), y: Math.random(), vx: (Math.random() - 0.5) * 0.0012, vy: (Math.random() - 0.5) * 0.0012
    }}));
    function resize() {{ canvas.width = window.innerWidth; canvas.height = window.innerHeight; }}
    window.addEventListener('resize', resize); resize();
    function frame() {{
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = '{accent}';
      for (const d of dots) {{
        d.x = (d.x + d.vx + 1) % 1; d.y = (d.y + d.vy + 1) % 1;
        ctx.beginPath(); ctx.arc(d.x * canvas.width, d.y * canvas.height, 2.0, 0, Math.PI * 2); ctx.fill();
      }}
      requestAnimationFrame(frame);
    }}
    requestAnimationFrame(frame);
    const fb = document.getElementById('cta-feedback');
    document.getElementById('primary-cta').addEventListener('click', () => {{
      fb.textContent = '{safe_title} workspace initialized — ready for configuration.';
    }});
    document.getElementById('status-btn').addEventListener('click', () => {{
      fb.textContent = 'Viewport ' + window.innerWidth + 'x' + window.innerHeight + ' · All components nominal.';
    }});
  </script>
</body>
</html>
"""


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Plugin entrypoint for single-prompt website generation."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "status").strip().lower()

    if action == "status":
        return f"Website Builder ready (templates={list(_TEMPLATES)}, out_root={_DEFAULT_SITES_DIR})."
    if action == "templates":
        return "Website Templates: " + ", ".join(f"{k} ({v[0]})" for k, v in _TEMPLATES.items())
    if action in ("build", "create"):
        prompt = str(params.get("prompt") or "Modern interactive web application").strip()
        title = str(params.get("title") or "JARVIS Web").strip()
        template = str(params.get("template") or "saas").strip().lower()
        out_dir = params.get("out_dir")
        if out_dir:
            folder = Path(out_dir)
        else:
            slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", title.lower()).strip("_")[:36] or "site"
            folder = _DEFAULT_SITES_DIR / f"{slug}_{time.strftime('%Y%m%d_%H%M%S')}"
        folder.mkdir(parents=True, exist_ok=True)
        index_path = folder / "index.html"
        html_doc = synthesize_website_html(prompt=prompt, title=title, template=template)
        index_path.write_text(html_doc, encoding="utf-8")

        preview_msg = ""
        if params.get("preview"):
            try:
                webbrowser.open(index_path.resolve().as_uri())
                preview_msg = " Opened in browser."
            except Exception as exc:
                preview_msg = f" Browser preview error: {exc}"
        return f"Built responsive HTML5/CSS/JS/Canvas website ({len(html_doc)} bytes): {index_path}.{preview_msg}"
    return f"Unknown website_builder action '{action}'. Use: status, build, templates."
