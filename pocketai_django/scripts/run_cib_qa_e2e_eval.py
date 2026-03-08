#!/usr/bin/env python
"""
End-to-end CIB QA evaluation runner (LLM + tools), with two-layer scoring:
  1) Strict token matching (legacy)
  2) Tolerant token matching (format-normalized)

Usage:
    python manage.py shell < scripts/run_cib_qa_e2e_eval.py

Optional env vars:
    BENCH_RUN_ID=...                     # stable run id for output filenames
    BENCH_RESUME_JSON=var/logs/...json   # resume from a partial run
    BENCH_BUSINESS_ID=...                # overrides default CIB tenant id
    BENCH_BASELINE_PATH=var/logs/...json # baseline file for comparison
"""

import json
import math
import os
import time
import traceback
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

from apps.accounts.models import AgentProfile, BusinessProfile
from apps.conversations.models import Conversation
from apps.llm.llm_provider import load_mcp_provider
from apps.mcp.orchestrator import McpOrchestratorService
from apps.rag.evaluation.token_matching import tokens_check


BASELINE_PATH = Path(os.environ.get("BENCH_BASELINE_PATH", "var/logs/cib_qa_e2e_eval_evidence_full.json"))
RUN_ID = os.environ.get("BENCH_RUN_ID") or datetime.now().strftime("%Y%m%d-%H%M%S")
OUT_JSON = Path(f"var/logs/cib_qa_e2e_eval_evidence_full_post_refactor_{RUN_ID}.json")
OUT_MD = Path(f"var/logs/cib_qa_e2e_eval_evidence_full_post_refactor_{RUN_ID}.md")
OUT_COMPARE = Path(f"var/logs/cib_qa_e2e_eval_comparison_post_refactor_{RUN_ID}.md")
RESUME_JSON = os.environ.get("BENCH_RESUME_JSON", "").strip()
BUSINESS_ID = os.environ.get("BENCH_BUSINESS_ID", "52885074-caa8-4983-aaf7-31bf3283bffd")
ALLOWED_TOOLS = {"search_knowledge", "read_knowledge"}


def percentile(values, p: float) -> float:
    if not values:
        return 0.0
    vals = sorted(float(v) for v in values)
    if len(vals) == 1:
        return vals[0]
    rank = (len(vals) - 1) * (p / 100.0)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return vals[lo]
    frac = rank - lo
    return vals[lo] * (1.0 - frac) + vals[hi] * frac


def extract_json_payload(text: str):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def gather_evidence_text(tool_trace):
    parts = []
    for entry in tool_trace or []:
        payload = ((entry.get("llm_response") or {}).get("content")) or ""
        if payload:
            parts.append(str(payload))
        summary = entry.get("output_summary") or {}
        for key in ("results_preview", "read_preview", "evidence_preview"):
            value = summary.get(key)
            if value:
                parts.append(json.dumps(value, ensure_ascii=False))
    return "\n".join(parts)


def gather_top_upload_ids(knowledge_payload, tool_trace):
    ordered = []
    seen = set()

    def add(value):
        if not value:
            return
        value = str(value)
        if value in seen:
            return
        seen.add(value)
        ordered.append(value)

    for item in knowledge_payload or []:
        if isinstance(item, dict):
            add(item.get("upload_id"))
    for entry in tool_trace or []:
        payload = extract_json_payload(((entry.get("llm_response") or {}).get("content")) or "")
        if not isinstance(payload, dict):
            continue
        for ref in payload.get("refs") or []:
            if isinstance(ref, dict):
                add(ref.get("document_id") or ref.get("upload_id"))
        for ev in payload.get("evidence") or []:
            if isinstance(ev, dict):
                add(ev.get("document_id") or ev.get("upload_id"))
    return ordered[:10]


def summarize(items):
    total = len(items)

    def score_answer_hit(it, mode: str) -> bool:
        tokens = list(it.get("required_tokens") or [])
        text = str(it.get("answer_text") or "")
        if tokens and text:
            hit, _missing = tokens_check(required_tokens=tokens, text=text, mode=mode)
            return hit
        if mode == "tolerant":
            return bool(it.get("answer_hit_tolerant") or it.get("answer_hit"))
        return bool(it.get("answer_hit"))

    def score_evidence_hit(it, mode: str) -> bool:
        tokens = list(it.get("required_tokens") or [])
        text = str(it.get("evidence_text") or "")
        if tokens and text:
            hit, _missing = tokens_check(required_tokens=tokens, text=text, mode=mode)
            return hit
        if mode == "tolerant":
            return bool(it.get("evidence_hit_tolerant") or it.get("evidence_hit"))
        return bool(it.get("evidence_hit"))

    answer_hits_strict = sum(1 for i in items if score_answer_hit(i, "strict"))
    evidence_hits_strict = sum(1 for i in items if score_evidence_hit(i, "strict"))
    either_hits_strict = sum(1 for i in items if score_answer_hit(i, "strict") or score_evidence_hit(i, "strict"))

    answer_hits_tolerant = sum(1 for i in items if score_answer_hit(i, "tolerant"))
    evidence_hits_tolerant = sum(1 for i in items if score_evidence_hit(i, "tolerant"))
    either_hits_tolerant = sum(
        1 for i in items if score_answer_hit(i, "tolerant") or score_evidence_hit(i, "tolerant")
    )

    doc_hits = sum(1 for i in items if i.get("doc_hit"))
    evidence_true_strict = [i for i in items if score_evidence_hit(i, "strict")]
    answer_given_evidence_strict = (
        sum(1 for i in evidence_true_strict if score_answer_hit(i, "strict")) / len(evidence_true_strict)
    ) if evidence_true_strict else 0.0

    evidence_true_tolerant = [i for i in items if score_evidence_hit(i, "tolerant")]
    answer_given_evidence_tolerant = (
        sum(1 for i in evidence_true_tolerant if score_answer_hit(i, "tolerant")) / len(evidence_true_tolerant)
    ) if evidence_true_tolerant else 0.0

    durations = [int(i.get("duration_ms") or 0) for i in items]
    tool_durations = [float(i.get("tool_total_ms") or 0.0) for i in items]
    search_counts = [int((i.get("tool_counts") or {}).get("search_knowledge", 0) or 0) for i in items]
    read_counts = [int((i.get("tool_counts") or {}).get("read_knowledge", 0) or 0) for i in items]

    by_diff = {}
    for diff in ("S", "M", "H"):
        subset = [i for i in items if i.get("difficulty") == diff]
        by_diff[diff] = {
            "total": len(subset),
            "answer_hits_strict": sum(1 for i in subset if score_answer_hit(i, "strict")),
            "answer_hits_tolerant": sum(1 for i in subset if score_answer_hit(i, "tolerant")),
        }

    return {
        "total": total,
        "answer_hit_rate_strict": answer_hits_strict / total if total else 0.0,
        "answer_hit_rate_tolerant": answer_hits_tolerant / total if total else 0.0,
        "evidence_hit_rate_strict": evidence_hits_strict / total if total else 0.0,
        "evidence_hit_rate_tolerant": evidence_hits_tolerant / total if total else 0.0,
        "either_hit_rate_strict": either_hits_strict / total if total else 0.0,
        "either_hit_rate_tolerant": either_hits_tolerant / total if total else 0.0,
        "expected_doc_hit_rate": doc_hits / total if total else 0.0,
        "answer_given_evidence_strict": answer_given_evidence_strict,
        "answer_given_evidence_tolerant": answer_given_evidence_tolerant,
        "answer_hits_strict": answer_hits_strict,
        "answer_hits_tolerant": answer_hits_tolerant,
        "evidence_hits_strict": evidence_hits_strict,
        "evidence_hits_tolerant": evidence_hits_tolerant,
        "either_hits_strict": either_hits_strict,
        "either_hits_tolerant": either_hits_tolerant,
        "doc_hits": doc_hits,
        "by_difficulty": by_diff,
        "latency_ms": {
            "avg": sum(durations) / total if total else 0.0,
            "p50": percentile(durations, 50),
            "p95": percentile(durations, 95),
            "p99": percentile(durations, 99),
            "max": max(durations) if durations else 0,
        },
        "tool_time_ms": {
            "avg": sum(tool_durations) / total if total else 0.0,
            "p50": percentile(tool_durations, 50),
            "p95": percentile(tool_durations, 95),
            "max": max(tool_durations) if tool_durations else 0.0,
        },
        "tool_usage": {
            "search_knowledge": {
                "avg": sum(search_counts) / total if total else 0.0,
                "p95": percentile(search_counts, 95),
                "max": max(search_counts) if search_counts else 0,
            },
            "read_knowledge": {
                "avg": sum(read_counts) / total if total else 0.0,
                "p95": percentile(read_counts, 95),
                "max": max(read_counts) if read_counts else 0,
            },
        },
    }


def fmt_pct(value):
    return f"{value * 100:.1f}%"


def fmt_ms(value):
    return f"{value / 1000:.1f}s"


def write_markdown(path: Path, summary_data, title: str):
    lines = [
        f"# {title}",
        "",
        f"Generated: {datetime.now().isoformat()}",
        "",
        f"- Total QAs: {summary_data['total']}",
        f"- Answer hit rate (strict): {fmt_pct(summary_data['answer_hit_rate_strict'])} ({summary_data['answer_hits_strict']}/{summary_data['total']})",
        f"- Answer hit rate (tolerant): {fmt_pct(summary_data['answer_hit_rate_tolerant'])} ({summary_data['answer_hits_tolerant']}/{summary_data['total']})",
        f"- Evidence hit rate (strict): {fmt_pct(summary_data['evidence_hit_rate_strict'])} ({summary_data['evidence_hits_strict']}/{summary_data['total']})",
        f"- Evidence hit rate (tolerant): {fmt_pct(summary_data['evidence_hit_rate_tolerant'])} ({summary_data['evidence_hits_tolerant']}/{summary_data['total']})",
        f"- Either hit (strict): {fmt_pct(summary_data['either_hit_rate_strict'])} ({summary_data['either_hits_strict']}/{summary_data['total']})",
        f"- Either hit (tolerant): {fmt_pct(summary_data['either_hit_rate_tolerant'])} ({summary_data['either_hits_tolerant']}/{summary_data['total']})",
        f"- Expected-doc hit rate: {fmt_pct(summary_data['expected_doc_hit_rate'])} ({summary_data['doc_hits']}/{summary_data['total']})",
        f"- Answer given evidence (strict): {fmt_pct(summary_data['answer_given_evidence_strict'])}",
        f"- Answer given evidence (tolerant): {fmt_pct(summary_data['answer_given_evidence_tolerant'])}",
        "",
        "## By difficulty (answer hit)",
        "",
        f"- Simple: strict {fmt_pct((summary_data['by_difficulty']['S']['answer_hits_strict'] / summary_data['by_difficulty']['S']['total']) if summary_data['by_difficulty']['S']['total'] else 0.0)} ({summary_data['by_difficulty']['S']['answer_hits_strict']}/{summary_data['by_difficulty']['S']['total']}), tolerant {fmt_pct((summary_data['by_difficulty']['S']['answer_hits_tolerant'] / summary_data['by_difficulty']['S']['total']) if summary_data['by_difficulty']['S']['total'] else 0.0)} ({summary_data['by_difficulty']['S']['answer_hits_tolerant']}/{summary_data['by_difficulty']['S']['total']})",
        f"- Moderate: strict {fmt_pct((summary_data['by_difficulty']['M']['answer_hits_strict'] / summary_data['by_difficulty']['M']['total']) if summary_data['by_difficulty']['M']['total'] else 0.0)} ({summary_data['by_difficulty']['M']['answer_hits_strict']}/{summary_data['by_difficulty']['M']['total']}), tolerant {fmt_pct((summary_data['by_difficulty']['M']['answer_hits_tolerant'] / summary_data['by_difficulty']['M']['total']) if summary_data['by_difficulty']['M']['total'] else 0.0)} ({summary_data['by_difficulty']['M']['answer_hits_tolerant']}/{summary_data['by_difficulty']['M']['total']})",
        f"- Hard: strict {fmt_pct((summary_data['by_difficulty']['H']['answer_hits_strict'] / summary_data['by_difficulty']['H']['total']) if summary_data['by_difficulty']['H']['total'] else 0.0)} ({summary_data['by_difficulty']['H']['answer_hits_strict']}/{summary_data['by_difficulty']['H']['total']}), tolerant {fmt_pct((summary_data['by_difficulty']['H']['answer_hits_tolerant'] / summary_data['by_difficulty']['H']['total']) if summary_data['by_difficulty']['H']['total'] else 0.0)} ({summary_data['by_difficulty']['H']['answer_hits_tolerant']}/{summary_data['by_difficulty']['H']['total']})",
        "",
        "## Latency (per turn)",
        "",
        f"- Avg {fmt_ms(summary_data['latency_ms']['avg'])}, p50 {fmt_ms(summary_data['latency_ms']['p50'])}, p95 {fmt_ms(summary_data['latency_ms']['p95'])}, p99 {fmt_ms(summary_data['latency_ms']['p99'])}, max {fmt_ms(summary_data['latency_ms']['max'])}",
        "  Tool time (inside turn):",
        f"- Avg {fmt_ms(summary_data['tool_time_ms']['avg'])}, p50 {fmt_ms(summary_data['tool_time_ms']['p50'])}, p95 {fmt_ms(summary_data['tool_time_ms']['p95'])}, max {fmt_ms(summary_data['tool_time_ms']['max'])}",
        "",
        "## Tool usage",
        "",
        f"- Avg search_knowledge {summary_data['tool_usage']['search_knowledge']['avg']:.2f} (p95={summary_data['tool_usage']['search_knowledge']['p95']:.0f}, max={summary_data['tool_usage']['search_knowledge']['max']})",
        f"- Avg read_knowledge {summary_data['tool_usage']['read_knowledge']['avg']:.2f} (p95={summary_data['tool_usage']['read_knowledge']['p95']:.0f}, max={summary_data['tool_usage']['read_knowledge']['max']})",
    ]
    path.write_text("\n".join(lines) + "\n")


baseline = json.loads(BASELINE_PATH.read_text())
qa_items = baseline["items"]
business = BusinessProfile.objects.get(id=BUSINESS_ID)
agent = AgentProfile.objects.filter(business_profile=business).order_by("created_at").first()
if agent is None:
    raise RuntimeError(f"No agent found for business {BUSINESS_ID}")
provider = load_mcp_provider()
if provider is None:
    raise RuntimeError("MCP provider is not configured")
orchestrator = McpOrchestratorService(agent=agent, provider=provider)
results = []
completed_ids = set()

if RESUME_JSON:
    resume_path = Path(RESUME_JSON)
    if resume_path.exists():
        resume_payload = json.loads(resume_path.read_text())
        loaded_items = list(resume_payload.get("items") or [])
        if loaded_items:
            results.extend(loaded_items)
            completed_ids = {
                str(item.get("id") or "").strip()
                for item in loaded_items
                if str(item.get("id") or "").strip()
            }
            if resume_path != OUT_JSON:
                OUT_JSON.write_text(
                    json.dumps(
                        {
                            "business_id": BUSINESS_ID,
                            "generated_at": datetime.now().isoformat(),
                            "qa_path": baseline.get("qa_path"),
                            "tool_whitelist": sorted(ALLOWED_TOOLS),
                            "baseline_path": str(BASELINE_PATH),
                            "resumed_from": str(resume_path),
                            "items": results,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )

print(f"START run_id={RUN_ID} resumed={len(results)}", flush=True)

for idx, item in enumerate(qa_items, start=1):
    item_id = str(item.get("id") or "").strip()
    if item_id and item_id in completed_ids:
        continue
    question = item["question"]
    required_tokens = list(item.get("required_tokens") or [])
    started = time.perf_counter()
    conversation = Conversation.objects.create(
        business_profile=business,
        agent_profile=agent,
        session_token=f"cib-bench-{RUN_ID}-{idx}-{uuid.uuid4().hex[:8]}",
    )
    error = None
    answer_text = ""
    tool_trace = []
    knowledge_payload = []
    try:
        context = orchestrator.stream_turn(
            conversation=conversation,
            user_message=question,
            allowed_tools=ALLOWED_TOOLS,
        )
        answer_text = context.response_text or ""
        tool_trace = list(context.tool_trace or [])
        knowledge_payload = list(context.knowledge_payload or [])
    except Exception as exc:
        error = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        print(f"ERROR id={item['id']} error={error}")
        print(traceback.format_exc())
    duration_ms = int((time.perf_counter() - started) * 1000)
    evidence_text = gather_evidence_text(tool_trace)
    answer_hit, answer_missing = tokens_check(required_tokens=required_tokens, text=answer_text, mode="strict")
    answer_hit_tolerant, answer_missing_tolerant = tokens_check(
        required_tokens=required_tokens, text=answer_text, mode="tolerant"
    )
    evidence_hit, evidence_missing = tokens_check(required_tokens=required_tokens, text=evidence_text, mode="strict")
    evidence_hit_tolerant, evidence_missing_tolerant = tokens_check(
        required_tokens=required_tokens, text=evidence_text, mode="tolerant"
    )
    top_upload_ids = gather_top_upload_ids(knowledge_payload, tool_trace)
    tool_counts = Counter()
    tool_total_ms = 0.0
    for entry in tool_trace:
        tool_name = entry.get("tool")
        if tool_name:
            tool_counts[str(tool_name)] += 1
        value = entry.get("duration_ms")
        if isinstance(value, (int, float)):
            tool_total_ms += float(value)

    results.append(
        {
            "id": item["id"],
            "difficulty": item.get("difficulty"),
            "question": question,
            "expected_answer": item.get("expected_answer"),
            "required_tokens": required_tokens,
            "source_doc": item.get("source_doc"),
            "source_page": item.get("source_page"),
            "expected_upload_id": item.get("expected_upload_id"),
            "status": "error" if error else "ok",
            "duration_ms": duration_ms,
            "answer_text": answer_text,
            "evidence_text": evidence_text[:40000],  # large enough for matching + debugging
            "answer_hit": answer_hit,
            "answer_missing_tokens": answer_missing,
            "evidence_hit": evidence_hit,
            "evidence_missing_tokens": evidence_missing,
            "answer_hit_tolerant": answer_hit_tolerant,
            "answer_missing_tokens_tolerant": answer_missing_tolerant,
            "evidence_hit_tolerant": evidence_hit_tolerant,
            "evidence_missing_tokens_tolerant": evidence_missing_tolerant,
            "doc_hit": str(item.get("expected_upload_id") or "") in set(top_upload_ids),
            "top_upload_ids": top_upload_ids,
            "tool_counts": dict(tool_counts),
            "tool_total_ms": round(tool_total_ms, 1),
            "error": error,
        }
    )

    payload = {
        "business_id": BUSINESS_ID,
        "generated_at": datetime.now().isoformat(),
        "qa_path": baseline.get("qa_path"),
        "tool_whitelist": sorted(ALLOWED_TOOLS),
        "baseline_path": str(BASELINE_PATH),
        "resumed_from": RESUME_JSON or None,
        "items": results,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    completed_ids.add(item_id)
    if len(results) % 5 == 0 or len(results) == len(qa_items):
        partial = summarize(results)
        print(
            f"PROGRESS {len(results)}/{len(qa_items)} answer={partial['answer_hits_strict']}/{len(results)} evidence={partial['evidence_hits_strict']}/{len(results)} "
            f"doc={partial['doc_hits']}/{len(results)} avg_latency_s={partial['latency_ms']['avg'] / 1000:.1f}",
            flush=True,
        )

new_summary = summarize(results)
old_summary = summarize(qa_items)
old_by_id = {item["id"]: item for item in qa_items}
new_by_id = {item["id"]: item for item in results}
answer_improved = sorted(k for k, v in new_by_id.items() if v.get("answer_hit") and not old_by_id[k].get("answer_hit"))
answer_regressed = sorted(k for k, v in new_by_id.items() if old_by_id[k].get("answer_hit") and not v.get("answer_hit"))
evidence_improved = sorted(k for k, v in new_by_id.items() if v.get("evidence_hit") and not old_by_id[k].get("evidence_hit"))
evidence_regressed = sorted(k for k, v in new_by_id.items() if old_by_id[k].get("evidence_hit") and not v.get("evidence_hit"))

write_markdown(OUT_MD, new_summary, "CIB QA E2E Eval Evidence Full (Post-refactor)")

comparison_lines = [
    "# CIB QA E2E Comparison (Post-refactor vs Pre-refactor)",
    "",
    f"- Baseline file: {BASELINE_PATH.name}",
    f"- New file: {OUT_JSON.name}",
    "",
    "## Metrics",
    "",
    f"- Answer hit rate (strict): {fmt_pct(old_summary['answer_hit_rate_strict'])} -> {fmt_pct(new_summary['answer_hit_rate_strict'])} ({(new_summary['answer_hit_rate_strict'] - old_summary['answer_hit_rate_strict']) * 100:+.1f} pp)",
    f"- Answer hit rate (tolerant): {fmt_pct(old_summary['answer_hit_rate_tolerant'])} -> {fmt_pct(new_summary['answer_hit_rate_tolerant'])} ({(new_summary['answer_hit_rate_tolerant'] - old_summary['answer_hit_rate_tolerant']) * 100:+.1f} pp)",
    f"- Evidence hit rate (strict): {fmt_pct(old_summary['evidence_hit_rate_strict'])} -> {fmt_pct(new_summary['evidence_hit_rate_strict'])} ({(new_summary['evidence_hit_rate_strict'] - old_summary['evidence_hit_rate_strict']) * 100:+.1f} pp)",
    f"- Evidence hit rate (tolerant): {fmt_pct(old_summary['evidence_hit_rate_tolerant'])} -> {fmt_pct(new_summary['evidence_hit_rate_tolerant'])} ({(new_summary['evidence_hit_rate_tolerant'] - old_summary['evidence_hit_rate_tolerant']) * 100:+.1f} pp)",
    f"- Either hit (strict): {fmt_pct(old_summary['either_hit_rate_strict'])} -> {fmt_pct(new_summary['either_hit_rate_strict'])} ({(new_summary['either_hit_rate_strict'] - old_summary['either_hit_rate_strict']) * 100:+.1f} pp)",
    f"- Either hit (tolerant): {fmt_pct(old_summary['either_hit_rate_tolerant'])} -> {fmt_pct(new_summary['either_hit_rate_tolerant'])} ({(new_summary['either_hit_rate_tolerant'] - old_summary['either_hit_rate_tolerant']) * 100:+.1f} pp)",
    f"- Expected-doc hit rate: {fmt_pct(old_summary['expected_doc_hit_rate'])} -> {fmt_pct(new_summary['expected_doc_hit_rate'])} ({(new_summary['expected_doc_hit_rate'] - old_summary['expected_doc_hit_rate']) * 100:+.1f} pp)",
    f"- Answer given evidence (strict): {fmt_pct(old_summary['answer_given_evidence_strict'])} -> {fmt_pct(new_summary['answer_given_evidence_strict'])} ({(new_summary['answer_given_evidence_strict'] - old_summary['answer_given_evidence_strict']) * 100:+.1f} pp)",
    f"- Answer given evidence (tolerant): {fmt_pct(old_summary['answer_given_evidence_tolerant'])} -> {fmt_pct(new_summary['answer_given_evidence_tolerant'])} ({(new_summary['answer_given_evidence_tolerant'] - old_summary['answer_given_evidence_tolerant']) * 100:+.1f} pp)",
    "",
    "## Difficulty deltas (answer hit)",
    "",
    (
        f"- Simple: strict {old_summary['by_difficulty']['S']['answer_hits_strict']}/{old_summary['by_difficulty']['S']['total']} -> "
        f"{new_summary['by_difficulty']['S']['answer_hits_strict']}/{new_summary['by_difficulty']['S']['total']}, "
        f"tolerant {old_summary['by_difficulty']['S']['answer_hits_tolerant']}/{old_summary['by_difficulty']['S']['total']} -> "
        f"{new_summary['by_difficulty']['S']['answer_hits_tolerant']}/{new_summary['by_difficulty']['S']['total']}"
    ),
    (
        f"- Moderate: strict {old_summary['by_difficulty']['M']['answer_hits_strict']}/{old_summary['by_difficulty']['M']['total']} -> "
        f"{new_summary['by_difficulty']['M']['answer_hits_strict']}/{new_summary['by_difficulty']['M']['total']}, "
        f"tolerant {old_summary['by_difficulty']['M']['answer_hits_tolerant']}/{old_summary['by_difficulty']['M']['total']} -> "
        f"{new_summary['by_difficulty']['M']['answer_hits_tolerant']}/{new_summary['by_difficulty']['M']['total']}"
    ),
    (
        f"- Hard: strict {old_summary['by_difficulty']['H']['answer_hits_strict']}/{old_summary['by_difficulty']['H']['total']} -> "
        f"{new_summary['by_difficulty']['H']['answer_hits_strict']}/{new_summary['by_difficulty']['H']['total']}, "
        f"tolerant {old_summary['by_difficulty']['H']['answer_hits_tolerant']}/{old_summary['by_difficulty']['H']['total']} -> "
        f"{new_summary['by_difficulty']['H']['answer_hits_tolerant']}/{new_summary['by_difficulty']['H']['total']}"
    ),
    "",
    "## Latency deltas",
    "",
    f"- Avg turn latency: {fmt_ms(old_summary['latency_ms']['avg'])} -> {fmt_ms(new_summary['latency_ms']['avg'])}",
    f"- p50 turn latency: {fmt_ms(old_summary['latency_ms']['p50'])} -> {fmt_ms(new_summary['latency_ms']['p50'])}",
    f"- p95 turn latency: {fmt_ms(old_summary['latency_ms']['p95'])} -> {fmt_ms(new_summary['latency_ms']['p95'])}",
    f"- Avg tool time: {fmt_ms(old_summary['tool_time_ms']['avg'])} -> {fmt_ms(new_summary['tool_time_ms']['avg'])}",
    "",
    "## Tool usage deltas",
    "",
    f"- Avg search_knowledge: {old_summary['tool_usage']['search_knowledge']['avg']:.2f} -> {new_summary['tool_usage']['search_knowledge']['avg']:.2f}",
    f"- Max search_knowledge: {old_summary['tool_usage']['search_knowledge']['max']} -> {new_summary['tool_usage']['search_knowledge']['max']}",
    f"- Avg read_knowledge: {old_summary['tool_usage']['read_knowledge']['avg']:.2f} -> {new_summary['tool_usage']['read_knowledge']['avg']:.2f}",
    "",
    "## Per-item flips (strict)",
    "",
    f"- Answer improved: {len(answer_improved)}",
    f"- Answer regressed: {len(answer_regressed)}",
    f"- Evidence improved: {len(evidence_improved)}",
    f"- Evidence regressed: {len(evidence_regressed)}",
    "",
    f"- Answer improved IDs: {', '.join(answer_improved[:30]) or 'none'}",
    f"- Answer regressed IDs: {', '.join(answer_regressed[:30]) or 'none'}",
    f"- Evidence improved IDs: {', '.join(evidence_improved[:30]) or 'none'}",
    f"- Evidence regressed IDs: {', '.join(evidence_regressed[:30]) or 'none'}",
]
OUT_COMPARE.write_text("\n".join(comparison_lines) + "\n")

print("DONE", flush=True)
print(
    json.dumps(
        {
            "run_id": RUN_ID,
            "out_json": str(OUT_JSON),
            "out_md": str(OUT_MD),
            "out_compare": str(OUT_COMPARE),
            # Backward-compatible keys (strict), plus explicit strict/tolerant fields.
            "answer_hit_rate": round(new_summary["answer_hit_rate_strict"], 4),
            "evidence_hit_rate": round(new_summary["evidence_hit_rate_strict"], 4),
            "either_hit_rate": round(new_summary["either_hit_rate_strict"], 4),
            "answer_hit_rate_strict": round(new_summary["answer_hit_rate_strict"], 4),
            "answer_hit_rate_tolerant": round(new_summary["answer_hit_rate_tolerant"], 4),
            "evidence_hit_rate_strict": round(new_summary["evidence_hit_rate_strict"], 4),
            "evidence_hit_rate_tolerant": round(new_summary["evidence_hit_rate_tolerant"], 4),
            "either_hit_rate_strict": round(new_summary["either_hit_rate_strict"], 4),
            "either_hit_rate_tolerant": round(new_summary["either_hit_rate_tolerant"], 4),
            "expected_doc_hit_rate": round(new_summary["expected_doc_hit_rate"], 4),
            "answer_given_evidence_strict": round(new_summary["answer_given_evidence_strict"], 4),
            "answer_given_evidence_tolerant": round(new_summary["answer_given_evidence_tolerant"], 4),
            "avg_latency_ms": round(new_summary["latency_ms"]["avg"], 1),
            "avg_search_calls": round(new_summary["tool_usage"]["search_knowledge"]["avg"], 2),
            "avg_read_calls": round(new_summary["tool_usage"]["read_knowledge"]["avg"], 2),
        },
        ensure_ascii=False,
    ),
    flush=True,
)

