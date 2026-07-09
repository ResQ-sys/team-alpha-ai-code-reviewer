"""
PDF export for a finished code review.

Turns the pipeline's ``final_report_json`` into a self-contained, shareable PDF:
an infographic header (quality/confidence/finding KPIs), consolidated pie/bar
charts, and a clearly listed, severity-grouped set of findings with file:line,
CWE, code snippet, and — when the review ran against a GitHub repo — a clickable
deep link straight to the offending line on GitHub.

Depends on reportlab (PDF) + utils.charts (matplotlib PNG charts).
"""
from __future__ import annotations

import html
import io
from datetime import datetime
from typing import Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from utils import charts
from utils.github_repo import GitHubRepo

_SEV_HEX = {
    "CRITICAL": "#b71c1c",
    "ERROR": "#e53935",
    "WARNING": "#fb8c00",
    "INFO": "#1e88e5",
}


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("H0", parent=ss["Title"], fontSize=20, spaceAfter=6))
    ss.add(ParagraphStyle("Meta", parent=ss["Normal"], fontSize=8,
                           textColor=colors.grey))
    ss.add(ParagraphStyle("SecH", parent=ss["Heading2"], fontSize=13,
                           spaceBefore=14, spaceAfter=6,
                           textColor=colors.HexColor("#263238")))
    ss.add(ParagraphStyle("Find", parent=ss["Normal"], fontSize=9, leading=12,
                          spaceAfter=2, alignment=TA_LEFT))
    ss.add(ParagraphStyle("Snip", parent=ss["Code"], fontSize=7.5, leading=9,
                          backColor=colors.HexColor("#f5f5f5"),
                          borderPadding=4, leftIndent=6))
    return ss


def _kpi_cards(story, ss, report: Dict):
    """Infographic KPI row: quality, confidence, findings, files."""
    sev = report.get("severity_breakdown", {})
    total = sum(sev.values())
    cards = [
        ("Quality Score", f"{report.get('quality_score', 0)}/100", "#1565c0"),
        ("Confidence", f"{report.get('confidence_score', 0)}", "#6a1b9a"),
        ("Total Findings", str(total), "#c62828"),
        ("Files Analyzed", str(report.get("files_analyzed", 0)), "#2e7d32"),
    ]
    cell_style = ParagraphStyle("kpi", parent=ss["Normal"], alignment=1)
    row = []
    for label, value, hexc in cards:
        txt = (f'<font size=16 color="{hexc}"><b>{html.escape(value)}</b></font>'
               f'<br/><font size=8 color="#555555">{html.escape(label)}</font>')
        row.append(Paragraph(txt, cell_style))
    tbl = Table([row], colWidths=[4.2 * cm] * 4)
    tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cfd8dc")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cfd8dc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fafafa")),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 10))


def _charts_block(story, findings: List[Dict], quality_issues: List[Dict]):
    """Two rows of paired charts embedded as PNG images."""
    sev = charts.severity_counts(findings)
    cat = charts.category_counts(findings, quality_issues)
    src = charts.source_counts(findings)
    rules = charts.top_rules(findings)

    pngs = [
        charts.pie_png(sev, "Findings by Severity", charts.SEVERITY_COLORS),
        charts.pie_png(cat, "Consolidated Issue Types",
                       charts.CATEGORY_COLORS, charts.CATEGORY_LABELS),
        charts.bar_png(src, "Findings by Detector"),
        charts.bar_png(rules, "Top Rules Triggered"),
    ]
    imgs = [Image(io.BytesIO(p), width=7.6 * cm, height=6 * cm) for p in pngs if p]
    # Lay out two per row.
    for i in range(0, len(imgs), 2):
        pair = imgs[i:i + 2]
        tbl = Table([pair], colWidths=[8 * cm] * len(pair))
        tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(tbl)
        story.append(Spacer(1, 6))


def _severity_chip(sev: str) -> str:
    hexc = _SEV_HEX.get(sev, "#607d8b")
    return f'<font color="{hexc}"><b>[{html.escape(sev)}]</b></font>'


def _findings_block(story, ss, findings: List[Dict], gh: Optional[GitHubRepo]):
    if not findings:
        story.append(Paragraph("No bug or vulnerability findings detected. 🎉",
                               ss["Find"]))
        return

    order = {s: i for i, s in enumerate(charts.SEVERITY_ORDER)}
    findings = sorted(findings, key=lambda f: order.get(f.get("severity", "INFO"), 9))

    for f in findings:
        sev = f.get("severity", "INFO")
        file = f.get("file", "?")
        line = f.get("line", "?")
        end_line = f.get("end_line")
        rule = f.get("rule_id", "")
        loc = f"{file}:{line}"

        if gh:
            url = gh.blob_url(file, f.get("line"), end_line)
            loc_html = f'<link href="{html.escape(url)}"><font color="#1565c0"><u>{html.escape(gh.rel_path(file))}:{line}</u></font></link>'
        else:
            loc_html = f"<font face='Courier'>{html.escape(loc)}</font>"

        header = (f"{_severity_chip(sev)} {loc_html} "
                  f"&mdash; <font face='Courier' size=8>{html.escape(rule)}</font>")
        story.append(Paragraph(header, ss["Find"]))
        story.append(Paragraph(html.escape(f.get("message", "")), ss["Find"]))

        meta = []
        if f.get("cwe"):
            meta.append("CWE: " + ", ".join(f["cwe"]))
        if f.get("owasp"):
            meta.append("OWASP: " + ", ".join(f["owasp"]))
        if meta:
            story.append(Paragraph(
                f'<font size=7 color="#777777">{html.escape("  |  ".join(meta))}</font>',
                ss["Find"]))

        snippet = (f.get("code_snippet") or "").strip()
        if snippet and snippet.lower() != "requires login":
            story.append(Preformatted(snippet[:500], ss["Snip"]))
        story.append(Spacer(1, 6))


def build_pdf(report: Dict, gh: Optional[GitHubRepo] = None) -> bytes:
    """Render the review report to PDF bytes."""
    ss = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            topMargin=1.4 * cm, bottomMargin=1.4 * cm,
                            leftMargin=1.6 * cm, rightMargin=1.6 * cm,
                            title="AI Code Review Report")
    story = []

    story.append(Paragraph("🛡️ AI Code Review Report", ss["H0"]))
    if gh:
        repo_line = (f'Repository: <link href="{html.escape(gh.url)}">'
                     f'<font color="#1565c0"><u>{html.escape(gh.full_name)}</u></font></link> '
                     f'&nbsp;(<font face="Courier">{html.escape(gh.ref)}</font>)')
    else:
        repo_line = f"Repository: <font face='Courier'>{html.escape(str(report.get('repo_path', '')))}</font>"
    story.append(Paragraph(repo_line, ss["Meta"]))
    story.append(Paragraph(
        f"Generated {report.get('generated_at', datetime.utcnow().isoformat())}",
        ss["Meta"]))
    story.append(Spacer(1, 10))

    _kpi_cards(story, ss, report)

    findings = report.get("bug_and_vulnerability_findings", [])
    quality_issues = report.get("quality_issues", [])

    story.append(Paragraph("Visual Summary", ss["SecH"]))
    _charts_block(story, findings, quality_issues)

    story.append(Paragraph("Bug &amp; Vulnerability Findings", ss["SecH"]))
    _findings_block(story, ss, findings, gh)

    if quality_issues:
        story.append(Paragraph("Code Quality Issues", ss["SecH"]))
        for q in quality_issues:
            story.append(Paragraph(
                f"{_severity_chip(q.get('severity', 'INFO'))} "
                f"<font face='Courier' size=8>{html.escape(q.get('file','?'))}:"
                f"{q.get('line','?')}</font> &mdash; {html.escape(q.get('message',''))}",
                ss["Find"]))

    fixes = report.get("suggested_fixes", [])
    if fixes:
        story.append(Paragraph("Suggested Fixes", ss["SecH"]))
        for fx in fixes:
            status = "verified" if fx.get("verified") else "flagged by verifier"
            story.append(Paragraph(
                f"<b>{html.escape(fx.get('file','?'))}:{fx.get('line','?')}</b> "
                f"<font size=7 color='#777777'>({status})</font><br/>"
                f"{html.escape(fx.get('explanation',''))}", ss["Find"]))
            if fx.get("fixed_code"):
                story.append(Preformatted(fx["fixed_code"][:500], ss["Snip"]))
            story.append(Spacer(1, 4))

    doc.build(story)
    buf.seek(0)
    return buf.read()
