"""Evidence pack generator: reads ACT's own tables, renders Markdown and PDF in 2026-54 order."""
import html
import json
import logging
import os
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
from sqlalchemy import func, select

from . import chain, db, governance
from .normalize import _audit

log = logging.getLogger(__name__)
SCHEMA = json.loads((Path(__file__).resolve().parent.parent / "schemas" / "evidence-pack.json").read_text())
MODEL_RE = re.compile(r"[\w.:/-]*(?:claude|anthropic|sonnet|haiku|opus)[\w.:/-]*", re.I)
TITLES = [("identity_ownership", "Identity and ownership"), ("tier_criteria", "Tier and criteria"),
          ("permitted_actions", "Permitted actions"), ("validation", "Validation"),
          ("monitoring_findings", "Monitoring and findings"), ("approvals_overrides", "Approvals and overrides"),
          ("records_retention", "Records and retention")]


class AgentNotFound(Exception):
    pass


def clean(v):
    """Never emit vendor model ids; collapse newlines so values stay on one Markdown line."""
    if v is None:
        return ""
    return re.sub(r"\s+", " ", MODEL_RE.sub("frontier model", str(v))).strip()


def fmt_ts(ts):
    if not ts:
        return "-"
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return clean(ts)


def evidence_dir() -> Path:
    if os.environ.get("ACT_EVIDENCE_DIR"):
        return Path(os.environ["ACT_EVIDENCE_DIR"])
    project = os.environ.get("DOMINO_PROJECT_NAME")
    if project and os.environ.get("DOMINO_IS_GIT_BASED", "").lower() == "true":
        return Path(f"/mnt/data/{project}/act/evidence")
    if project and os.environ.get("DOMINO_API_PROXY"):
        return Path(f"/domino/datasets/local/{project}/act/evidence")
    return Path("./evidence")


def safe_id(agent_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", agent_id).strip("-")


def _c(source, ref):
    return {"source": source, "ref": f"{source}:{ref}"}


def _bullets(items):
    return "\n".join(f"- {i}" for i in items)


def _load(agent_id):
    with db.engine.connect() as cx:
        a = cx.execute(select(db.agents).where(db.agents.c.id == agent_id)).first()
        if a is None:
            raise AgentNotFound(agent_id)
        q = lambda t, order=None: cx.execute(select(t).where(t.c.agent_id == agent_id).order_by(t.c.id if order is None else order)).all()  # noqa: E731
        d = dict(agent=a, identities=q(db.identities), manifests=q(db.manifests), findings=q(db.findings),
                 approvals=q(db.approvals), overrides=q(db.overrides), validation=q(db.validation_records),
                 deps=q(db.dependencies), runs=q(db.runs, db.runs.c.started_at))
        d["event_counts"] = cx.execute(select(db.events.c.run_id, func.count()).where(
            db.events.c.agent_id == agent_id).group_by(db.events.c.run_id)).all()
        d["audit"] = cx.execute(select(db.audit_events).where(db.audit_events.c.target == agent_id)
                                .order_by(db.audit_events.c.id.desc()).limit(1)).all()
    return d


def _identity(d, aid):
    a = d["agent"]
    rec = a.record or {}
    ident = rec.get("identity") or {}
    lines = [f"Agent: {clean(a.name)} ({clean(aid)})", f"Source platform: {clean(a.source_platform)}",
             f"Owner: {clean(a.owner) or '-'}", f"Registered by: {clean(a.registered_by_username) or 'not registered'}",
             f"Status: {clean(a.status)}", f"Last seen: {fmt_ts(a.last_seen)} UTC",
             f"Identity binding: {clean(ident.get('type')) or 'none'}"
             + (f", issuer {clean(ident.get('issuer'))}" if ident.get("issuer") else "")]
    cites = [_c("agents", aid)]
    for i in d["identities"]:
        lines.append(f"Principal: {clean(i.principal) or '-'} (kind {clean(i.kind) or '-'}, "
                     f"scopes {clean(', '.join(map(str, i.scopes or []))) or 'none'})")
        cites.append(_c("identities", i.id))
    if not d["identities"]:
        lines.append("No distinct identity recorded: the agent acts under a shared or borrowed credential.")
    models = [clean(x.name) for x in d["deps"] if x.kind == "model"]
    if models:
        lines.append("Models (gateway alias or neutral label): " + ", ".join(models))
    return _bullets(lines), cites


def _tier(d, aid):
    a = d["agent"]
    crit = a.criteria or {}
    lines = [f"Tier: {clean(a.tier)}", f"Reasoning: {clean(a.tier_reasoning) or 'none recorded'}"]
    lines += [f"Criterion {clean(k).replace('_', ' ')}: {clean(v)}" for k, v in crit.items()]
    return _bullets(lines), [_c("agents", aid)]


def _actions(d, aid):
    cites = [_c("agents", aid)]
    if not d["manifests"]:
        return "No manifest recorded.", cites
    m = d["manifests"][-1]
    c = m.content or {}
    lim = c.get("value_limits_detail") or c.get("value_limits") or {}
    lines = [f"Manifest version: {clean(m.version) or '-'}",
             "Tools: " + (", ".join(clean(t) for t in c.get("tools") or []) or "none"),
             "Data scopes: " + (", ".join(clean(t) for t in c.get("data_scopes") or []) or "none"),
             "Systems: " + (", ".join(clean(t) for t in c.get("systems") or []) or "none"),
             "Value limits: " + ("; ".join(
                 f"{clean(k)} {clean(v.get('amount') if isinstance(v, dict) else v)}"
                 f"{' ' + clean(v.get('currency')) if isinstance(v, dict) and v.get('currency') else ''}"
                 for k, v in lim.items()) or "none")]
    cites += [_c("manifests", x.id) for x in d["manifests"]]
    return _bullets(lines), cites


def _validation(d, aid):
    recs = d["validation"]
    if not recs:
        return "No validation record exists for this agent.", [_c("agents", aid)]
    cites = [_c("validation_records", r.id) for r in recs]
    lines = []
    for r in recs:
        det = r.detail or {}
        scores = [float(x) for x in det.get("scores") or [] if isinstance(x, (int, float))]
        parts = [f"{clean(r.kind)} on {fmt_ts(r.created_at)} UTC, result {clean(r.result)}"]
        mean = det.get("mean", statistics.fmean(scores) if scores else None)
        if mean is not None:
            parts.append(f"mean {float(mean):.3f}")
        p5, p95 = det.get("p5"), det.get("p95")
        if scores and len(scores) >= 2:
            qs = statistics.quantiles(scores, n=20, method="inclusive")
            p5 = p5 if p5 is not None else qs[0]
            p95 = p95 if p95 is not None else qs[-1]
        if p5 is not None:
            parts.append(f"p5 {float(p5):.3f}")
        if p95 is not None:
            parts.append(f"p95 {float(p95):.3f}")
        pr = det.get("pass_rate")
        if pr is not None:
            parts.append(f"pass rate {float(pr):.1%}")
        if det.get("judge_alias") or det.get("judge_version"):
            parts.append(f"judge {clean(det.get('judge_alias'))} version {clean(det.get('judge_version'))}")
        lines.append(", ".join(parts))
    return _bullets(lines), cites


def _monitoring(d, aid):
    fs = d["findings"]
    cites = [_c("findings", f.id) for f in fs]
    lines = []
    for f in sorted(fs, key=lambda f: (f.status != "open", -f.id)):
        det = f.detail or {}
        run = f.run_id
        lines.append(f"**Finding {f.id}** ({'open' if f.status == 'open' else 'closed'}): type {clean(f.type or f.rule)}, "
                     f"severity {clean(f.severity)}, tool {clean(f.tool) or '-'}, span {clean(f.span_id) or '-'}, "
                     f"time {fmt_ts(f.ts)} UTC")
        viols = det.get("violations") or []
        if viols:
            for v in viols:
                lines.append(f"  - Violation: tool {clean(v.get('tool'))}, reason {clean(v.get('reason'))}, "
                             f"observed {clean(v.get('observed_value', v.get('observed')))} vs limit "
                             f"{clean(v.get('limit'))}")
        else:
            lines.append(f"  - Violation: tool {clean(f.tool)}, reason {clean(f.reason)}, observed "
                         f"{clean(f.observed_value)} vs limit {clean(f.limit)}")
        lines.append(f"  - Explanation: {clean(det.get('explanation') or f.message) or 'none'}")
        if run:
            lines.append(f"  - Timeline: runs/{clean(run)}/timeline (events:{clean(run)})")
            cites.append(_c("events", run))
    if not fs:
        lines.append("No findings recorded for this agent.")
        cites = [_c("agents", aid)]
    return _bullets(lines) if fs else lines[0], _dedupe(cites)


def _dedupe(cites):
    seen, out = set(), []
    for c in cites:
        if c["ref"] not in seen:
            seen.add(c["ref"])
            out.append(c)
    return out


def _approvals(d, aid):
    lines, cites = [], []
    for a in d["approvals"]:
        lines.append(f"Approval by {clean(a.approver)}: decision {clean(a.decision)}, date {fmt_ts(a.created_at)} UTC, "
                     f"expires {fmt_ts(a.expires_at)} UTC" + (f", note {clean(a.note)}" if a.note else ""))
        cites.append(_c("approvals", a.id))
    for o in d["overrides"]:
        lines.append(f"Override by {clean(o.actor_username)} on {fmt_ts(o.ts)} UTC (run {clean(o.run_id) or '-'}): "
                     f"reason {clean(o.reason)}")
        cites.append(_c("overrides", o.id))
    if not lines:
        return "No approval or override has been recorded.", [_c("agents", aid)]
    return _bullets(lines), cites


def _records(d, aid, paths=None):
    v = chain.verify()
    counts = d["event_counts"]
    total = sum(n for _, n in counts)
    lines = [f"Hash chain: {'verified intact' if v['ok'] else 'BROKEN'} over {v['events']} events (whole ledger)",
             f"Events for this agent: {total} across {len(counts)} runs",
             "Retention: records are kept in an append-only SHA-256 hash chain; WORM storage is out of scope for this POC."]
    cites = [_c("events", r) for r, _ in counts if r]
    if d["audit"]:
        cites.append(_c("audit_events", d["audit"][0].id))
    cites.append(_c("agents", aid))
    if paths:
        lines.append(f"Files (under the evidence directory in the project dataset): {paths['markdown']} and {paths['pdf']}")
        lines.append("Bundle: " + (f"attached to Domino Governance bundle {paths['bundle']}" if paths.get("bundle")
                                   else "no Governance bundle for this agent; saved to the project dataset"))
    return _bullets(lines), _dedupe(cites)


def build_pack(agent_id: str, generated_by: str = "", paths: dict | None = None) -> dict:
    d = _load(agent_id)
    builders = [_identity, _tier, _actions, _validation, _monitoring, _approvals]
    sections = []
    for (sid, title), fn in zip(TITLES, builders + [lambda d, a: _records(d, a, paths)]):
        content, cites = fn(d, agent_id)
        sections.append({"id": sid, "title": title, "content": content, "citations": cites})
    pack = {"agent_id": agent_id, "agent_name": clean(d["agent"].name),
            "generated_at": datetime.now(timezone.utc).isoformat(), "generated_by": clean(generated_by) or "unknown",
            "sections": sections}
    jsonschema.validate(pack, SCHEMA)
    return pack


def render_markdown(pack: dict) -> str:
    out = [f"# Evidence pack for {pack['agent_name']}", "",
           f"- Agent: {pack['agent_name']} ({pack['agent_id']})",
           f"- Generated at: {fmt_ts(pack['generated_at'])} UTC",
           f"- Generated by: {pack['generated_by']}", ""]
    for s in pack["sections"]:
        out += [f"## {s['title']}", "", s["content"], "",
                "Sources: " + ", ".join(f"`{c['ref']}`" for c in s["citations"]), ""]
    return "\n".join(out)


CSS = ("body{font-family:sans-serif;font-size:10pt;line-height:1.4}h1{font-size:18pt}"
       "h2{font-size:13pt;border-bottom:1px solid #999;margin-top:18pt}code{font-size:8.5pt;word-break:break-all}"
       "li{margin:2pt 0}@page{margin:18mm}")


def _pdf_weasy(md: str, dest: Path):
    from markdown_it import MarkdownIt
    from weasyprint import HTML
    body = MarkdownIt("commonmark", {"html": False}).render(md)
    HTML(string=f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>"
         ).write_pdf(str(dest))


def _pdf_reportlab(md: str, dest: Path):
    """Pure-Python fallback (reportlab, BSD) when WeasyPrint's system libraries are missing."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    st = getSampleStyleSheet()
    flow = []
    for line in md.splitlines():
        if not line.strip():
            flow.append(Spacer(1, 4))
            continue
        m = re.match(r"^(#{1,2})\s+(.*)", line)
        t = html.escape(m.group(2) if m else line.lstrip())
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t).replace("`", "")
        if m:
            flow.append(Paragraph(t, st["Heading1" if len(m.group(1)) == 1 else "Heading2"]))
        else:
            ind = len(line) - len(line.lstrip())
            flow.append(Paragraph(("&bull; " if line.lstrip().startswith("- ") else "") +
                                  t.removeprefix("- "), st["BodyText"] if not ind else st["Bullet"]))
    SimpleDocTemplate(str(dest), pagesize=A4).build(flow)


def render_pdf(md: str, dest: Path) -> str:
    try:
        _pdf_weasy(md, dest)
        return "weasyprint"
    except Exception:
        log.warning("WeasyPrint unavailable, using reportlab fallback", exc_info=True)
        _pdf_reportlab(md, dest)
        return "reportlab"


def generate(agent_id: str, user_id: str, username: str) -> dict:
    d = _load(agent_id)  # 404 early
    bundle_id = d["agent"].bundle_id or None
    now = datetime.now(timezone.utc)
    folder = evidence_dir() / safe_id(agent_id)
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"evidence-pack-{safe_id(agent_id)}-{now.strftime('%Y%m%dT%H%M%S%fZ')}"
    md_path, pdf_path = folder / f"{stem}.md", folder / f"{stem}.pdf"
    pack = build_pack(agent_id, username, {"markdown": f"{folder.name}/{md_path.name}", "pdf": f"{folder.name}/{pdf_path.name}",
                                   "bundle": bundle_id})
    md = render_markdown(pack)
    md_path.write_text(md, encoding="utf-8")
    renderer = render_pdf(md, pdf_path)
    with db.engine.begin() as cx:
        pack_id = cx.execute(db.evidence_packs.insert().values(
            agent_id=agent_id, created_at=pack["generated_at"], created_by=username, path=str(md_path),
            content=pack)).inserted_primary_key[0]
        _audit(cx, "evidence_pack_generated", agent_id,
               {"pack_id": pack_id, "markdown_path": str(md_path), "pdf_path": str(pdf_path), "renderer": renderer},
               actor=(user_id, username))
    governance_note = None
    if bundle_id:
        summary = (f"ACT evidence pack {pack_id} for {pack['agent_name']} generated {fmt_ts(pack['generated_at'])} UTC "
                   f"by {pack['generated_by']}: seven sections in 2026-54 order. Files: {md_path.name}, {pdf_path.name}.")
        try:
            governance.attach_evidence(bundle_id, "evidence_pack", {
                "summary": summary, "path": str(folder), "citation": f"evidence_packs:{pack_id} at {folder}"})
            governance_note = "attached"
        except Exception as e:
            log.warning("policy evidence submit failed (%s); trying a Report attachment", e)
            try:  # best effort: a bundle attachment pointing at the dataset file
                governance.attach_evidence(bundle_id, "report", {
                    "name": f"ACT evidence pack {pack_id}", "summary": summary,
                    "identifier": {"source": "dataset", "filename": f"{folder.name}/{pdf_path.name}",
                                   "pack_id": pack_id}})
                governance_note = "attached as report"
            except Exception as e2:
                log.exception("attaching evidence pack to governance bundle failed")
                governance_note = f"attach failed: {clean(str(e2))[:200]}"
    return {"markdown_path": str(md_path), "pdf_path": str(pdf_path), "bundle_id": bundle_id, "pack_id": pack_id,
            "markdown": md, "governance": governance_note, "pdf_renderer": renderer}
