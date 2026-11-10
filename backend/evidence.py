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

from . import chain, db, demo, governance
from .normalize import _audit

log = logging.getLogger(__name__)
SCHEMA = json.loads((Path(__file__).resolve().parent.parent / "schemas" / "evidence-pack.json").read_text())
# The vendor word must start a word (so "corpus" is safe) but may run on into letters/digits (claude3, ClaudeSonnet4).
MODEL_RE = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z0-9]+[.:/_-])*(?:claude|anthropic|sonnet|haiku|opus)"
                      r"[A-Za-z0-9]*(?:[.:/_-][A-Za-z0-9]+)*", re.I)
_MD_META = re.compile(r"[\\!\[\]()`*#|&]|(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])")
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


def esc(v):
    """clean() plus Markdown escaping, so agent- or trace-controlled text stays inert text."""
    s = _MD_META.sub(lambda m: "\\" + m.group(0), clean(v))
    return s.replace("<", "&lt;").replace(">", "&gt;")


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
    return "\n".join(i if i.startswith("  - ") else f"- {i}" for i in items)


def _load(agent_id):
    with db.engine.connect() as cx:
        a = cx.execute(select(db.agents).where(db.agents.c.id == agent_id)).first()
        if a is None:
            raise AgentNotFound(agent_id)
        q = lambda t, order=None: cx.execute(select(t).where(t.c.agent_id == agent_id).order_by(t.c.id if order is None else order)).all()  # noqa: E731
        d = dict(agent=a, identities=q(db.identities), manifests=q(db.manifests), findings=q(db.findings),
                 approvals=q(db.approvals), overrides=q(db.overrides), validation=q(db.validation_records),
                 deps=q(db.dependencies), runs=q(db.runs, db.runs.c.started_at))
        d["audit"] = cx.execute(select(db.audit_events).where(db.audit_events.c.target == agent_id)
                                .order_by(db.audit_events.c.id.desc()).limit(1)).all()
    return d


def agent_runs(agent_id):
    """All runs of an agent, newest first: run rows plus run ids seen only in events; span_count = events."""
    with db.engine.connect() as cx:
        rows = cx.execute(select(db.runs).where(db.runs.c.agent_id == agent_id)).all()
        ev = cx.execute(select(db.events.c.run_id, func.count(), func.min(db.events.c.ts), func.max(db.events.c.ts))
                        .where(db.events.c.agent_id == agent_id).group_by(db.events.c.run_id)).all()
    counts = {r: (n, lo, hi) for r, n, lo, hi in ev if r}
    out = [{"run_id": r.id, "started_at": r.started_at, "ended_at": r.ended_at,
            "span_count": counts.pop(r.id, (0,))[0]} for r in rows]
    out += [{"run_id": r, "started_at": lo, "ended_at": hi, "span_count": n} for r, (n, lo, hi) in counts.items()]
    out.sort(key=lambda x: (_instant(x["started_at"]), x["run_id"]), reverse=True)
    return out


def _instant(ts):
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:
        return 0.0


def _identity(d, aid):
    a = d["agent"]
    rec = a.record or {}
    ident = rec.get("identity") or {}
    lines = [f"Agent: {esc(a.name)} ({esc(aid)})", f"Source platform: {esc(a.source_platform)}",
             f"Owner: {esc(a.owner) or '-'}", f"Registered by: {esc(a.registered_by_username) or 'not registered'}",
             f"Status: {esc(a.status)}", f"Last seen: {fmt_ts(a.last_seen)} UTC",
             f"Identity binding: {esc(ident.get('type')) or 'none'}"
             + (f", issuer {esc(ident.get('issuer'))}" if ident.get("issuer") else "")]
    cites = [_c("agents", aid)]
    for i in d["identities"]:
        lines.append(f"Principal: {esc(i.principal) or '-'} (kind {esc(i.kind) or '-'}, "
                     f"scopes {esc(', '.join(map(str, i.scopes or []))) or 'none'})")
        cites.append(_c("identities", i.id))
    if ident.get("simulated"):
        lines.append(f"Identity label: {demo.SERVICE_ACCOUNT_LABEL}")
    if not d["identities"]:
        lines.append("No distinct identity recorded: the agent acts under a shared or borrowed credential.")
    models = [x for x in d["deps"] if x.kind == "model"]
    if models:
        lines.append("Models (gateway alias or neutral label): " + ", ".join(esc(x.name) for x in models))
        cites += [_c("dependencies", x.id) for x in models]
    return _bullets(lines), cites


def _tier(d, aid):
    a = d["agent"]
    crit = a.criteria or {}
    lines = [f"Tier: {esc(a.tier)}", f"Reasoning: {esc(a.tier_reasoning) or 'none recorded'}"]
    lines += [f"Criterion {esc(k).replace('_', ' ')}: {esc(v)}" for k, v in crit.items()]
    return _bullets(lines), [_c("agents", aid)]


def _actions(d, aid):
    cites = [_c("agents", aid)]
    if not d["manifests"]:
        return "No manifest recorded.", cites
    m = d["manifests"][-1]
    c = m.content or {}
    lim = c.get("value_limits_detail") or c.get("value_limits") or {}
    lines = [f"Manifest version: {esc(m.version) or '-'}",
             "Tools: " + (", ".join(esc(t) for t in c.get("tools") or []) or "none"),
             "Data scopes: " + (", ".join(esc(t) for t in c.get("data_scopes") or []) or "none"),
             "Systems: " + (", ".join(esc(t) for t in c.get("systems") or []) or "none"),
             "Value limits: " + ("; ".join(
                 f"{esc(k)} {esc(v.get('amount') if isinstance(v, dict) else v)}"
                 f"{' ' + esc(v.get('currency')) if isinstance(v, dict) and v.get('currency') else ''}"
                 for k, v in lim.items()) or "none")]
    cites += [_c("manifests", x.id) for x in d["manifests"]]
    return _bullets(lines), cites


def _alias(v):
    """A gateway alias (for example sonnet) is a configured name, not a vendor model id: keep it, but only if
    it is a plain token and not a raw vendor id."""
    v = str(v or "")
    if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", v) and not re.search(r"claude|anthropic|\d{6,}", v, re.I):
        return v
    return esc(v) or "not recorded"


def _validation(d, aid):
    recs = d["validation"]
    if not recs:
        return "No validation record exists for this agent.", [_c("agents", aid)]
    cites = [_c("validation_records", r.id) for r in recs]
    lines = []
    for r in recs:
        det = r.detail or {}
        scores = [float(x) for x in det.get("scores") or [] if isinstance(x, (int, float))]
        parts = [f"{esc(r.kind)} on {fmt_ts(r.created_at)} UTC, result {esc(r.result)}"]
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
        if det.get("n_prompts") and det.get("reps"):
            parts.append(f"{int(det['n_prompts'])} prompts x {int(det['reps'])} repetitions")
        if det.get("judge_model_id") == "mock-llm":
            parts.append("mock judge (not a model) — rerun with the LLM Gateway for a model-graded record")
        elif det.get("judge_alias") or det.get("judge_model_id") or det.get("judge_version"):
            parts.append(f"judge: frontier model via the LLM gateway; alias and version on file in "
                         f"validation_records:{r.id}")
        if det.get("error_count") is not None or det.get("invalid_count") is not None:
            parts.append(f"{int(det.get('error_count') or 0)} errors and {int(det.get('invalid_count') or 0)} "
                         f"invalid judge replies")
        if det.get("experiment"):
            n = len(det.get("mlflow_run_ids") or [])
            parts.append(f"MLflow experiment {esc(det['experiment'])}, {n} runs")
        lines.append(", ".join(parts))
    return _bullets(lines), cites


def _violation(v):
    """One violation line: observed/limit only when recorded; both for value_limit_exceeded."""
    reason = v.get("reason")
    obs = v.get("observed_value", v.get("observed"))
    lim = v.get("limit")
    label = {"tool_not_in_manifest": "Outside manifest", "value_limit_exceeded": "Over limit",
             "value_unverifiable": "Unverifiable value"}.get(reason, "Other violation")
    parts = [f"tool {esc(v.get('tool'))}", f"reason {label}"]
    have = lambda x: x is not None and x != ""  # noqa: E731
    if have(obs) or reason == "value_limit_exceeded":
        parts.append(f"observed {esc(obs) if have(obs) else 'not recorded'}")
    if have(lim) or reason == "value_limit_exceeded":
        parts.append(f"limit {esc(lim) if have(lim) else 'not recorded'}")
    if v.get("note"):
        parts.append(f"note {esc(v.get('note'))}")
    return "Violation: " + ", ".join(parts)


def _monitoring(d, aid):
    fs = d["findings"]
    cites = [_c("findings", f.id) for f in fs]
    lines = []
    for f in sorted(fs, key=lambda f: (f.status != "open", -f.id)):
        det = f.detail or {}
        run = f.run_id
        lines.append(f"**Finding {f.id}** ({'open' if f.status == 'open' else 'closed'}): type {esc(f.type or f.rule)}, "
                     f"severity {esc(f.severity)}, tool {esc(f.tool) or '-'}, span {esc(f.span_id) or '-'}, "
                     f"time {fmt_ts(f.ts)} UTC")
        viols = det.get("violations") or [dict(tool=f.tool, reason=f.reason, observed_value=f.observed_value,
                                               limit=f.limit)]
        for v in viols:
            lines.append("  - " + _violation(v))
        mock = det.get("explanation_model") == "mock-llm"
        lines.append(f"  - Explanation{' (template text from the mock explainer, not a model)' if mock else ''}: "
                     f"{esc(det.get('explanation') or f.message) or 'none'}")
        if run:
            lines.append(f"  - Timeline: runs/{esc(run)}/timeline")
            cites.append(_c("events", run))
    if any((f.detail or {}).get("explanation_model") == "mock-llm" for f in fs):
        lines.append("Note: explanations marked mock were produced by the mock explainer (ACT_LLM_PROVIDER=mock), "
                     "a template, not a model.")
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
        sim = " [simulated approval (demo stand-in for a second Domino user)]" if "simulated" in (a.approver or "").lower() else ""
        lines.append(f"Approval by {esc(a.approver)}{sim}: decision {esc(a.decision)}, date {fmt_ts(a.created_at)} UTC, "
                     f"expires {fmt_ts(a.expires_at)} UTC" + (f", note {esc(a.note)}" if a.note else ""))
        cites.append(_c("approvals", a.id))
    for o in d["overrides"]:
        lines.append(f"Override by {esc(o.actor_username)} on {fmt_ts(o.ts)} UTC (run {esc(o.run_id) or '-'}): "
                     f"reason {esc(o.reason)}")
        cites.append(_c("overrides", o.id))
    if not lines:
        return "No approval or override has been recorded.", [_c("agents", aid)]
    return _bullets(lines), cites


def _records(d, aid, paths=None):
    v = chain.verify()
    runs = agent_runs(aid)
    total = sum(r["span_count"] for r in runs)
    lines = [f"Hash chain: {'verified intact' if v['ok'] else 'BROKEN'} over {v['events']} events (whole ledger)",
             f"Events for this agent: {total} events across {len(runs)} runs",
             "Retention: records are kept in an append-only SHA-256 hash chain; WORM storage is out of scope for this POC."]
    cites = [_c("events", r["run_id"]) for r in runs]
    if d["audit"]:
        cites.append(_c("audit_events", d["audit"][0].id))
    cites.append(_c("agents", aid))
    if paths:
        lines.append(f"Files: {esc(paths['markdown'])} and {esc(paths['pdf'])}, saved in the project dataset at "
                     f"{esc(paths['location'])}")
        if demo.enabled():
            lines.append(f"Governance: {demo.GOVERNANCE_LABEL}; no call was made to Domino Governance")
        lines.append("Governance: this pack is recorded against bundle " + esc(paths["bundle"]) if paths.get("bundle")
                     else "Governance: no Governance bundle for this agent; the pack is recorded in the project dataset only")
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
    out = [f"# Evidence pack for {esc(pack['agent_name'])}", "",
           f"- Agent: {esc(pack['agent_name'])} ({esc(pack['agent_id'])})",
           f"- Generated at: {fmt_ts(pack['generated_at'])} UTC",
           f"- Generated by: {esc(pack['generated_by'])}", ""]
    for s in pack["sections"]:
        refs = [re.sub(r"[`\s]+", " ", clean(c["ref"])) for c in s["citations"]]
        out += [f"## {s['title']}", "", s["content"], "", "Sources: " + ", ".join(f"`{r}`" for r in refs), ""]
    return "\n".join(out)


CSS = ("body{font-family:sans-serif;font-size:10pt;line-height:1.4}h1{font-size:18pt}"
       "h2{font-size:13pt;border-bottom:1px solid #999;margin-top:18pt}code{font-size:8.5pt;word-break:break-all}"
       "li{margin:2pt 0}@page{margin:18mm}")


def _refuse_fetch(url, *a, **k):
    raise ValueError(f"evidence pack PDF rendering never fetches resources: {url}")


def _pdf_weasy(md: str, dest: Path):
    from markdown_it import MarkdownIt
    from weasyprint import HTML
    body = MarkdownIt("commonmark", {"html": False}).disable(["image", "link", "autolink"]).render(md)
    HTML(string=f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>",
         url_fetcher=_refuse_fetch).write_pdf(str(dest))


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
        t = m.group(2) if m else line.lstrip()
        t = html.escape(t.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"))
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t).replace("`", "")
        t = re.sub(r"\\([\\!\[\]()*_#|&])", r"\1", t)
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


class RenderError(Exception):
    pass


def _dataset_location(folder_name: str) -> str:
    project = os.environ.get("DOMINO_PROJECT_NAME")
    return f"{project + '/' if project else ''}act/evidence/{folder_name}/"


def generate(agent_id: str, user_id: str, username: str) -> dict:
    d = _load(agent_id)  # 404 early
    bundle_id = d["agent"].bundle_id or None
    now = datetime.now(timezone.utc)
    folder = evidence_dir() / safe_id(agent_id)
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"evidence-pack-{safe_id(agent_id)}-{now.strftime('%Y%m%dT%H%M%S%fZ')}"
    md_path, pdf_path = folder / f"{stem}.md", folder / f"{stem}.pdf"
    md_tmp, pdf_tmp = folder / f"{stem}.md.tmp", folder / f"{stem}.pdf.tmp"
    # The pack is written before the attach attempt, so it records the target bundle, never an outcome.
    pack = build_pack(agent_id, username, {"markdown": f"{folder.name}/{md_path.name}", "pdf": f"{folder.name}/{pdf_path.name}",
                                           "bundle": bundle_id, "location": _dataset_location(folder.name)})
    md = render_markdown(pack)
    try:
        md_tmp.write_text(md, encoding="utf-8")
        renderer = render_pdf(md, pdf_tmp)
        os.replace(pdf_tmp, pdf_path)
        os.replace(md_tmp, md_path)
    except Exception as e:
        log.exception("evidence pack rendering failed")
        for p in (md_tmp, pdf_tmp, md_path, pdf_path):
            p.unlink(missing_ok=True)
        raise RenderError("evidence pack PDF rendering failed") from e
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
                   f"by {pack['generated_by']}: seven sections in 2026-54 order. Files are saved in the project "
                   f"dataset at {_dataset_location(folder.name)}: {md_path.name}, {pdf_path.name}.")
        detail = {"pack_id": pack_id, "bundle_id": bundle_id, "location": _dataset_location(folder.name)}
        try:
            governance.attach_evidence(bundle_id, "evidence_pack", {
                "summary": summary, "path": str(folder), "citation": f"evidence_packs:{pack_id} at {folder}"})
            governance_note, action = "attached", "evidence_pack_attached"
        except Exception as e:
            log.exception("policy evidence submit failed")
            governance_note, action = "attach_failed", "evidence_pack_attach_failed"
            detail["error"] = clean(str(e))[:200]
        detail["outcome"] = governance_note
        with db.engine.begin() as cx:
            _audit(cx, action, agent_id, detail, actor=(user_id, username))
    return {"markdown_path": str(md_path), "pdf_path": str(pdf_path), "bundle_id": bundle_id, "pack_id": pack_id,
            "markdown": md, "governance": governance_note, "pdf_renderer": renderer}
