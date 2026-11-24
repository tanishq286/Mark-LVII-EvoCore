"""plugins/office_suite.py — Full openpyxl Excel Assistant, PDF table-to-CSV, & Batch Format Converter."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import subprocess

PLUGIN = {
    "name": "office_suite",
    "brief": "Excel create/append/update/filter/format, PDF table-to-CSV, and batch document/image converter",
    "description": (
        "Handles full Excel workbook operations via openpyxl (create, read, append, update cells, "
        "filter rows, auto-fit and style headers), exports PDF tables to CSV via pdfplumber, "
        "and batch converts docx/xlsx/pptx/pdf/images across formats. Actions: status, excel_create, "
        "excel_read, excel_append, excel_update, excel_filter, excel_format, pdf_tables_to_csv, batch_convert."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "Action: status | excel_create | excel_read | excel_append | excel_update | "
                    "excel_filter | excel_format | pdf_tables_to_csv | batch_convert"
                ),
            },
            "path": {
                "type": "STRING",
                "description": "Target workbook/PDF path.",
            },
            "paths": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "List of source paths for batch_convert.",
            },
            "sheet": {
                "type": "STRING",
                "description": "Worksheet name (default active sheet).",
            },
            "headers": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "Column headers for excel_create.",
            },
            "rows": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "List of row lists for excel_create or excel_append.",
            },
            "cell": {
                "type": "STRING",
                "description": "Cell coordinate e.g. 'B3' for excel_update.",
            },
            "value": {
                "type": "STRING",
                "description": "Value to write into cell.",
            },
            "column": {
                "type": "STRING",
                "description": "Column name or 1-based index to filter in excel_filter.",
            },
            "equals": {
                "type": "STRING",
                "description": "Filter value match.",
            },
            "target_format": {
                "type": "STRING",
                "description": "Destination format extension e.g. 'pdf', 'csv', 'txt', 'png', 'jpg', 'webp'.",
            },
            "out_dir": {
                "type": "STRING",
                "description": "Optional output directory for exports/conversions.",
            },
        },
        "required": [],
    },
}


def excel_create(path: str | Path, headers: list, rows: list[list], *, sheet: str = "Sheet1") -> str:
    import openpyxl
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = str(sheet or "Sheet1")[:31]
    if headers:
        ws.append(list(headers))
    for r in (rows or []):
        ws.append(list(r))
    wb.save(str(p))
    return f"Created Excel workbook '{p}' with {len(rows or [])} data rows."


def excel_read(path: str | Path, *, sheet: str = "", limit: int = 50) -> str:
    import openpyxl
    p = Path(path)
    if not p.is_file():
        return f"Workbook not found: {p}"
    wb = openpyxl.load_workbook(str(p), data_only=True)
    ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.active
    rows = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i >= limit:
            break
        rows.append(["" if v is None else str(v) for v in row])
    if not rows:
        return f"Sheet '{ws.title}' is empty."
    return "\n".join(" | ".join(r) for r in rows)


def excel_append(path: str | Path, rows: list[list], *, sheet: str = "") -> str:
    import openpyxl
    p = Path(path)
    if not p.is_file():
        return f"Workbook not found: {p}"
    wb = openpyxl.load_workbook(str(p))
    ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.active
    for r in (rows or []):
        ws.append(list(r))
    wb.save(str(p))
    return f"Appended {len(rows or [])} rows to '{p}' (sheet '{ws.title}')."


def excel_update(path: str | Path, cell: str, value, *, sheet: str = "") -> str:
    import openpyxl
    p = Path(path)
    if not p.is_file():
        return f"Workbook not found: {p}"
    wb = openpyxl.load_workbook(str(p))
    ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.active
    ws[cell.upper()] = value
    wb.save(str(p))
    return f"Updated {ws.title}!{cell.upper()} = {value!r} in '{p}'."


def excel_filter(path: str | Path, column: str | int, equals: str, *, sheet: str = "") -> str:
    import openpyxl
    p = Path(path)
    if not p.is_file():
        return f"Workbook not found: {p}"
    wb = openpyxl.load_workbook(str(p), data_only=True)
    ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.active
    all_rows = list(ws.iter_rows(values_only=True))
    if not all_rows:
        return "Sheet is empty."
    hdr = [str(c or "") for c in all_rows[0]]
    col_idx: int | None = None
    if isinstance(column, int) or str(column).isdigit():
        idx = int(column) - 1
        if 0 <= idx < len(hdr):
            col_idx = idx
    else:
        low = str(column).strip().lower()
        for idx, h in enumerate(hdr):
            if h.strip().lower() == low:
                col_idx = idx
                break
    if col_idx is None:
        return f"Column '{column}' not found in headers {hdr}."
    want = str(equals).strip().lower()
    matched = [r for r in all_rows[1:] if str(r[col_idx] if col_idx < len(r) else "").strip().lower() == want]
    if not matched:
        return f"No rows where {hdr[col_idx]} == '{equals}'."
    lines = [" | ".join(hdr)] + [" | ".join("" if v is None else str(v) for v in r) for r in matched[:50]]
    return "\n".join(lines)


def excel_format(path: str | Path, *, sheet: str = "") -> str:
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    p = Path(path)
    if not p.is_file():
        return f"Workbook not found: {p}"
    wb = openpyxl.load_workbook(str(p))
    ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.active
    hdr_fill = PatternFill(start_color="0F4060", end_color="0F4060", fill_type="solid")
    hdr_font = Font(bold=True, color="E0F8FF")
    thin = Side(style="thin", color="3A8A9A")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for row_idx, row in enumerate(ws.iter_rows(), start=1):
        for cell in row:
            cell.border = border
            cell.alignment = Alignment(vertical="center")
            if row_idx == 1:
                cell.fill = hdr_fill
                cell.font = hdr_font

    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=8)
        col_letter = col[0].column_letter
        ws.column_dimensions[col_letter].width = min(48, max(10, max_len + 4))

    ws.freeze_panes = "A2"
    wb.save(str(p))
    return f"Formatted workbook '{p}' (styled headers, borders, auto-fit columns)."


def pdf_tables_to_csv(pdf_path: str | Path, *, out_dir: str | Path | None = None) -> str:
    import pdfplumber
    src = Path(pdf_path)
    if not src.is_file():
        return f"PDF not found: {src}"
    dest = Path(out_dir) if out_dir else src.parent
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with pdfplumber.open(str(src)) as pdf:
        t_idx = 0
        for page_no, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables() or []
            for tbl in tables:
                t_idx += 1
                csv_path = dest / f"{src.stem}_table_{t_idx}_p{page_no}.csv"
                with open(csv_path, "w", newline="", encoding="utf-8") as fh:
                    writer = csv.writer(fh)
                    for row in tbl:
                        writer.writerow([("" if c is None else str(c).strip()) for c in row])
                written.append(csv_path)
    if not written:
        return f"No structured tables detected in '{src.name}'."
    return f"Exported {len(written)} table(s) to CSV: " + ", ".join(p.name for p in written[:10])


def _write_lines_to_pdf_fallback(lines: list[str], out_pdf: Path) -> None:
    """Create a clean text-rendered multi-page PDF without LibreOffice via Pillow."""
    from PIL import Image, ImageDraw, ImageFont
    w, h = 1240, 1754
    margin_x, margin_y, line_h = 80, 90, 32
    font = ImageFont.load_default()
    pages: list[Image.Image] = []
    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)
    y = margin_y

    for raw in lines:
        text = str(raw or "")
        chunks = [text[i : i + 95] for i in range(0, max(1, len(text)), 95)]
        for ch in chunks:
            if y > h - margin_y:
                pages.append(img)
                img = Image.new("RGB", (w, h), "white")
                draw = ImageDraw.Draw(img)
                y = margin_y
            draw.text((margin_x, y), ch, fill="black", font=font)
            y += line_h
    pages.append(img)
    pages[0].save(str(out_pdf), "PDF", resolution=150.0, save_all=True, append_images=pages[1:])


def _convert_single_file(src: Path, target_ext: str, out_dir: Path | None = None) -> Path:
    fmt = target_ext.lower().lstrip(".")
    dest_dir = out_dir if out_dir else src.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"{src.stem}.{fmt}"
    s_ext = src.suffix.lower().lstrip(".")

    img_exts = {"png", "jpg", "jpeg", "webp", "bmp", "tiff"}
    if s_ext in img_exts and fmt in (img_exts | {"pdf"}):
        from PIL import Image
        with Image.open(str(src)) as im:
            mode_im = im.convert("RGB") if fmt in ("jpg", "jpeg", "pdf") else im
            mode_im.save(str(out), "PDF" if fmt == "pdf" else None)
        return out

    if s_ext == "xlsx" and fmt == "csv":
        import openpyxl
        wb = openpyxl.load_workbook(str(src), data_only=True)
        ws = wb.active
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            for row in ws.iter_rows(values_only=True):
                w.writerow([("" if v is None else v) for v in row])
        return out

    if s_ext in ("docx", "xlsx", "pptx") and fmt == "pdf":
        # Prefer LibreOffice if installed; fall back to clean structural text PDF
        try:
            res = subprocess.run(
                ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(dest_dir), str(src)],
                capture_output=True, text=True, timeout=30, shell=False,
            )
            if res.returncode == 0 and out.is_file():
                return out
        except Exception:
            pass
        lines: list[str] = [f"Document Export: {src.name}", "=" * 60]
        if s_ext == "docx":
            from docx import Document
            doc = Document(str(src))
            lines.extend(p.text for p in doc.paragraphs if p.text.strip())
        elif s_ext == "xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(str(src), data_only=True)
            for ws in wb.worksheets:
                lines.append(f"[Sheet: {ws.title}]")
                for r in ws.iter_rows(values_only=True):
                    lines.append(" | ".join("" if v is None else str(v) for v in r))
        elif s_ext == "pptx":
            from pptx import Presentation
            prs = Presentation(str(src))
            for idx, sl in enumerate(prs.slides, 1):
                lines.append(f"--- Slide {idx} ---")
                for sh in sl.shapes:
                    if sh.has_text_frame:
                        lines.append(sh.text_frame.text.strip())
        _write_lines_to_pdf_fallback(lines, out)
        return out

    if s_ext == "pptx" and fmt == "txt":
        from pptx import Presentation
        prs = Presentation(str(src))
        chunks = []
        for idx, sl in enumerate(prs.slides, 1):
            chunks.append(f"## Slide {idx}")
            for sh in sl.shapes:
                if sh.has_text_frame and sh.text_frame.text.strip():
                    chunks.append(sh.text_frame.text.strip())
        out.write_text("\n".join(chunks), encoding="utf-8")
        return out

    if s_ext == "csv" and fmt == "json":
        with open(src, "r", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        out.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        return out

    raise ValueError(f"Unsupported conversion: .{s_ext} -> .{fmt}")


def batch_convert(paths: list[str | Path], target_format: str, *, out_dir: str | Path | None = None) -> str:
    """Convert multiple documents/spreadsheets/images into `target_format`."""
    if not paths:
        return "Provide paths=[...] and target_format='...'."
    dest = Path(out_dir) if out_dir else None
    ok: list[str] = []
    errors: list[str] = []
    for raw in paths:
        src = Path(str(raw))
        if not src.is_file():
            errors.append(f"{src.name}: missing")
            continue
        try:
            out = _convert_single_file(src, target_format, out_dir=dest)
            ok.append(out.name)
        except Exception as exc:
            errors.append(f"{src.name}: {exc}")
    msg = f"Converted {len(ok)}/{len(paths)} file(s) to .{target_format.lstrip('.')}."
    if ok:
        msg += " Outputs: " + ", ".join(ok[:10])
    if errors:
        msg += " Errors: " + "; ".join(errors[:5])
    return msg


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Plugin entrypoint for Excel, PDF tables, and batch format conversion."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "status").strip().lower()

    if action == "status":
        return "Office Suite ready (openpyxl + pdfplumber + python-docx + python-pptx + Pillow)."
    path = params.get("path") or ""
    sheet = str(params.get("sheet") or "")
    if action == "excel_create":
        return excel_create(path, params.get("headers") or [], params.get("rows") or [], sheet=sheet or "Sheet1")
    if action == "excel_read":
        return excel_read(path, sheet=sheet)
    if action == "excel_append":
        return excel_append(path, params.get("rows") or [], sheet=sheet)
    if action == "excel_update":
        return excel_update(path, str(params.get("cell") or "A1"), params.get("value"), sheet=sheet)
    if action == "excel_filter":
        return excel_filter(path, params.get("column") or 1, str(params.get("equals") or ""), sheet=sheet)
    if action == "excel_format":
        return excel_format(path, sheet=sheet)
    if action == "pdf_tables_to_csv":
        return pdf_tables_to_csv(path, out_dir=params.get("out_dir"))
    if action == "batch_convert":
        return batch_convert(params.get("paths") or [], str(params.get("target_format") or "pdf"), out_dir=params.get("out_dir"))
    return f"Unknown office_suite action '{action}'."
