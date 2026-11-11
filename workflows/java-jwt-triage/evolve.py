"""Measurable self-improvement loop for workflows/java-jwt-triage.

Demonstrates Airframe's differential regression gate on 6 real closed auth0/java-jwt issues:
  Epoch 0 (Day-1 baseline)     -> 3/6 (50%)
  Epoch 1 (over-broad rule)    -> 4/6 with 2 regressions -> REJECTED & rolled back
  Epoch 2 (scoped codebase rule) -> 6/6 (100%, +3 fixed, 0 regressions) -> PROMOTED

Run offline (0 API keys):
  python3 workflows/java-jwt-triage/evolve.py

Run live against an LLM (e.g. GEMINI_API_KEY set):
  python3 workflows/java-jwt-triage/evolve.py --live -e gemini-2.0-flash
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from rich.console import Console
from rich.table import Table
import yaml

from airframe.adapters import ModelAdapter, ModelResponse, UsageMetadata, resolve_adapter
from airframe.cli import _format_case_prompt, evaluate_expected_assertions
from airframe.isolation.context import ContextPartitioner
from airframe.runner import AgentStepDecision, WorkflowRunner, load_contract_schema
from airframe.telemetry import CaseResult, DifferentialReport, RunScorecard, compare_scorecards
from airframe.tools.scoper import ContextBudgeter
from airframe.workflows.loader import WorkflowPack, load_workflow

PACK_DIR = Path(__file__).resolve().parent

BASELINE_PLAYBOOK = """# auth0/java-jwt Bug Triage Playbook (Day-1 Baseline)

Triage incoming `auth0/java-jwt` bug reports to the exact responsible subsystem and Java source file.
Before emitting `BugTriageReport`, call `inspect_java_jwt_package` to confirm the target path exists.

## Routing Rules
- Rule `crypto-ecdsa`: terms [`es256`, `es384`, `es512`, `ecdsa`, `r=0`] -> `algorithms` | `lib/src/main/java/com/auth0/jwt/algorithms/ECDSAAlgorithm.java`
- Rule `claim-check`: terms [`exp`, `aud`, `claim`] -> `core-verifier` | `lib/src/main/java/com/auth0/jwt/JWTVerifier.java`
- Rule `decode-default`: terms [`decode`, `base64`, `jwt`] -> `core-token` | `lib/src/main/java/com/auth0/jwt/JWTDecoder.java`
"""

OVERBROAD_PLAYBOOK = """# auth0/java-jwt Bug Triage Playbook (Candidate v1: Over-Broad Mutation)

Triage incoming `auth0/java-jwt` bug reports to the exact responsible subsystem and Java source file.
Before emitting `BugTriageReport`, call `inspect_java_jwt_package` to confirm the target path exists.

## Routing Rules
- Rule `crypto-ecdsa`: terms [`es256`, `es384`, `es512`, `ecdsa`, `r=0`] -> `algorithms` | `lib/src/main/java/com/auth0/jwt/algorithms/ECDSAAlgorithm.java`
- Rule `token-split`: terms [`splitting`, `segments`] -> `core-token` | `lib/src/main/java/com/auth0/jwt/TokenUtils.java`
- Rule `deser-all-claims`: terms [`exp`, `aud`, `claim`] -> `impl-codec` | `lib/src/main/java/com/auth0/jwt/impl/PayloadDeserializer.java`
- Rule `decode-default`: terms [`decode`, `base64`, `jwt`] -> `core-token` | `lib/src/main/java/com/auth0/jwt/JWTDecoder.java`
"""

REFINED_PLAYBOOK = """# auth0/java-jwt Bug Triage Playbook (Learned v2: Scoped Boundaries)

Triage incoming `auth0/java-jwt` bug reports to the exact responsible subsystem and Java source file.
Before emitting `BugTriageReport`, call `inspect_java_jwt_package` to confirm the target path exists.

## Codebase Boundaries Learned From Past Issues
1. `TokenUtils.splitToken` splits `header.payload.signature` on `.` BEFORE `JWTDecoder` decodes Base64.
2. `PayloadDeserializer` coerces JSON `NumericDate` values (`exp`/`nbf`/`iat`) and `aud` strings/arrays BEFORE verification.
3. `JWTVerifier` evaluates claim assertions (`now`, leeway, expected claims) AFTER payload deserialization.

## Routing Rules
- Rule `crypto-ecdsa`: terms [`es256`, `es384`, `es512`, `ecdsa`, `r=0`] -> `algorithms` | `lib/src/main/java/com/auth0/jwt/algorithms/ECDSAAlgorithm.java`
- Rule `token-split`: terms [`splitting`, `segments`] -> `core-token` | `lib/src/main/java/com/auth0/jwt/TokenUtils.java`
- Rule `payload-deser`: terms [`numericdate`, `non-numeric`, `json payload`, `getaudience()`] -> `impl-codec` | `lib/src/main/java/com/auth0/jwt/impl/PayloadDeserializer.java`
- Rule `verifier-assert`: terms [`now`, `leeway`, `expected`, `tokenexpiredexception`, `invalidclaimexception`, `exp`, `aud`] -> `core-verifier` | `lib/src/main/java/com/auth0/jwt/JWTVerifier.java`
- Rule `decode-default`: terms [`decode`, `base64`, `jwt`] -> `core-token` | `lib/src/main/java/com/auth0/jwt/JWTDecoder.java`
"""

_RULE_RE = re.compile(
    r"-\s*Rule\s+`([^`]+)`:\s*terms\s+\[([^\]]+)\]\s*->\s*`([^`]+)`\s*\|\s*`([^`]+)`"
)


class PlaybookGuidedJavaJwtAdapter(ModelAdapter):
    """Deterministic adapter that executes strictly by parsing the active triage playbook markdown."""

    def __init__(self, model_name: str = "playbook-sim") -> None:
        super().__init__(model_name=model_name)

    @property
    def provider_name(self) -> str:
        return "playbook-sim"

    @staticmethod
    def _parse_rules(playbook_text: str) -> list[tuple[str, list[str], str, str]]:
        rules: list[tuple[str, list[str], str, str]] = []
        for match in _RULE_RE.finditer(playbook_text):
            rule_id = match.group(1).strip()
            terms = [t.strip().strip("`'\" ").lower() for t in match.group(2).split(",") if t.strip()]
            subsystem = match.group(3).strip()
            file_path = match.group(4).strip()
            rules.append((rule_id, terms, subsystem, file_path))
        return rules

    @staticmethod
    def _extract_task(prompt: str) -> str:
        after = prompt.split("[TASK]\n", 1)[-1]
        return after.split("\n\n[AVAILABLE TOOLS]", 1)[0].strip()

    async def invoke(
        self,
        prompt: str,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        task_lower = self._extract_task(prompt).lower()
        rules = self._parse_rules(system_prompt or "")

        matched = ("fallback", "core-token", "lib/src/main/java/com/auth0/jwt/JWTDecoder.java")
        for rule_id, terms, subsystem, file_path in rules:
            if any(term in task_lower for term in terms):
                matched = (rule_id, subsystem, file_path)
                break

        rule_id, subsystem, file_path = matched
        already_inspected = "Tool 'inspect_java_jwt_package' returned:" in prompt

        if not already_inspected:
            decision = AgentStepDecision(
                thought=f"Playbook rule '{rule_id}' selects '{subsystem}'; verify files via tool.",
                action="call_tool",
                tool_name="inspect_java_jwt_package",
                arguments={"subsystem": subsystem},
            )
        else:
            decision = AgentStepDecision(
                thought=f"Confirmed target in '{subsystem}' via tool; emitting BugTriageReport.",
                action="final_answer",
                final_output={
                    "subsystem": subsystem,
                    "suspected_file": file_path,
                    "rationale": f"Applied playbook rule '{rule_id}' -> {file_path}.",
                },
            )

        content = f"```json\n{json.dumps(decision.model_dump(), indent=2)}\n```"
        return ModelResponse(
            content=content,
            model_name=self.model_name,
            provider=self.provider_name,
            usage=UsageMetadata(input_tokens=160, output_tokens=70, total_tokens=230, estimated_cost_usd=0.0001),
            latency_ms=4.0,
        )


@dataclass
class EpochOutcome:
    epoch: int
    version: str
    scorecard: RunScorecard
    playbook_tokens: int
    decision: str
    diff: DifferentialReport | None = None
    last_trace_file: str | None = None


@dataclass
class EvolutionResult:
    epochs: list[EpochOutcome] = field(default_factory=list)
    promoted_playbook: str = ""
    applied_to_disk: bool = False


def _stage_pack_copy(dst_dir: Path, playbook_text: str) -> WorkflowPack:
    for rel in ("workflow.yaml", "contracts.py", "tools.py", "sensors.py", "repo_index.json", "testbed.yaml"):
        shutil.copy2(PACK_DIR / rel, dst_dir / rel)
    (dst_dir / "guides").mkdir(parents=True, exist_ok=True)
    (dst_dir / "guides" / "triage_playbook.md").write_text(playbook_text, encoding="utf-8")
    return load_workflow(dst_dir)


async def _evaluate_playbook(
    playbook_text: str,
    adapter: ModelAdapter,
    *,
    label: str,
    trace_dir: Path,
) -> tuple[RunScorecard, str | None]:
    with tempfile.TemporaryDirectory(prefix="airframe_jwt_eval_") as tmp:
        pack = _stage_pack_copy(Path(tmp), playbook_text)
        cases = yaml.safe_load((pack.root_dir / "testbed.yaml").read_text(encoding="utf-8")) or []
        contract_schema = load_contract_schema(pack)
        tool_registry = pack.load_tools()
        runner = WorkflowRunner(trace_dir=trace_dir)

        results: list[CaseResult] = []
        last_trace_file: str | None = None
        for idx, case in enumerate(cases):
            cid = str(case.get("id", f"case-{idx+1}"))
            case_input = case.get("input", {})
            expected = case.get("expected_output") or case.get("expected") or {}
            prompt = _format_case_prompt(case_input)
            sensors = pack.load_sensors(case_input=case_input if isinstance(case_input, dict) else None)
            try:
                res = await runner.run(
                    pack,
                    prompt,
                    adapter,
                    contract_schema=contract_schema,
                    tool_registry=tool_registry,
                    sensors=sensors,
                    eval_context=case_input if isinstance(case_input, dict) else None,
                    context_partitioner=ContextPartitioner(
                        agent_id=f"bug-triager-{cid}",
                        storage_dir=Path(tmp) / "transcripts" / cid,
                    ),
                    fail_on_sensor_error=False,
                )
                last_trace_file = res.trace_file
                failures = [f"{r.sensor_name}: {r.message}" for r in res.sensor_results if not r.passed]
                if isinstance(expected, dict) and expected:
                    failures.extend(evaluate_expected_assertions(res.output, expected))
                results.append(
                    CaseResult(
                        case_id=cid,
                        passed=(len(failures) == 0),
                        latency_ms=res.latency_ms,
                        tokens=res.total_tokens,
                        cost_usd=res.estimated_cost_usd,
                        error="; ".join(failures) if failures else None,
                    )
                )
            except Exception as err:
                results.append(CaseResult(case_id=cid, passed=False, error=str(err)))

    return RunScorecard.from_results(label, results), last_trace_file


async def _propose_live_playbook(
    adapter: ModelAdapter,
    current_playbook: str,
    baseline_scorecard: RunScorecard,
) -> str:
    fixes = yaml.safe_load((PACK_DIR / "resolved_fixes.yaml").read_text(encoding="utf-8")) or {}
    failed_notes = []
    for r in baseline_scorecard.results:
        if not r.passed:
            fix = fixes.get(r.case_id, {})
            failed_notes.append(
                f"- Case {r.case_id}: error='{r.error}' | actual_fix='{fix.get('fixed_file')}' ({fix.get('root_cause')})"
            )

    repo_index = json.loads((PACK_DIR / "repo_index.json").read_text(encoding="utf-8"))
    key_files = "\n".join(f"- {p}: {m['subsystem']} — {m['role']}" for p, m in repo_index["files"].items())

    optimizer_prompt = (
        "Improve this concise bug-triage playbook WITHOUT breaking currently passing cases.\n"
        "Preserve existing working distinctions and scope new rules narrowly by symptom boundary.\n\n"
        f"Current Playbook:\n```markdown\n{current_playbook.strip()}\n```\n\n"
        f"Failed Cases Diagnosis:\n" + "\n".join(failed_notes) + "\n\n"
        f"Repository Index:\n{key_files}\n\n"
        "Return ONLY the updated markdown playbook (keep under 350 tokens)."
    )
    resp = await adapter.invoke(optimizer_prompt)
    text = resp.content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            text = "\n".join(lines[1:-1]).strip()
    return text + "\n"


async def run_evolution(
    *,
    live: bool = False,
    engine: str = "gemini-2.0-flash",
    apply: bool = False,
    trace_dir: Path | None = None,
) -> EvolutionResult:
    """Execute Baseline -> Candidate(s) -> Differential Gate evolution and return metrics."""
    active_trace_dir = trace_dir or (PACK_DIR.parents[1] / ".airframe" / "traces")
    playbook_file = PACK_DIR / "guides" / "triage_playbook.md"
    baseline_text = playbook_file.read_text(encoding="utf-8")

    adapter: ModelAdapter = resolve_adapter(engine) if live else PlaybookGuidedJavaJwtAdapter()

    base_sc, base_trace = await _evaluate_playbook(
        baseline_text, adapter, label="v0-baseline", trace_dir=active_trace_dir
    )
    outcomes = [
        EpochOutcome(
            epoch=0,
            version="v0-baseline",
            scorecard=base_sc,
            playbook_tokens=ContextBudgeter.estimate_tokens(baseline_text),
            decision="BASELINE",
            last_trace_file=base_trace,
        )
    ]

    if live:
        candidates = [("v1-optimizer", await _propose_live_playbook(adapter, baseline_text, base_sc))]
    else:
        candidates = [("v1-overbroad", OVERBROAD_PLAYBOOK), ("v2-refined", REFINED_PLAYBOOK)]

    active_playbook = baseline_text
    for idx, (ver_name, cand_text) in enumerate(candidates, start=1):
        cand_sc, cand_trace = await _evaluate_playbook(
            cand_text, adapter, label=ver_name, trace_dir=active_trace_dir
        )
        diff = compare_scorecards(base_sc, cand_sc)
        should_promote = (not diff.has_regressions) and (cand_sc.passed_cases > base_sc.passed_cases)
        decision = (
            "PROMOTED"
            if should_promote
            else (f"REJECTED (-{len(diff.regressions)} reg)" if diff.has_regressions else "REJECTED (no gain)")
        )
        if should_promote:
            active_playbook = cand_text
        outcomes.append(
            EpochOutcome(
                epoch=idx,
                version=ver_name,
                scorecard=cand_sc,
                playbook_tokens=ContextBudgeter.estimate_tokens(cand_text),
                decision=decision,
                diff=diff,
                last_trace_file=cand_trace,
            )
        )

    applied = False
    if apply and active_playbook != baseline_text:
        playbook_file.write_text(active_playbook, encoding="utf-8")
        applied = True

    return EvolutionResult(epochs=outcomes, promoted_playbook=active_playbook, applied_to_disk=applied)


def _render_report(result: EvolutionResult, *, live: bool, engine: str) -> None:
    console = Console()
    mode = f"live ({engine})" if live else "offline (PlaybookGuidedJavaJwtAdapter)"
    console.print(f"[bold cyan]auth0/java-jwt Triage Self-Improvement[/bold cyan] · mode: [green]{mode}[/green]\n")

    summary = Table(title="Measurable Playbook Evolution (6 Real Closed Issues)")
    summary.add_column("Epoch", style="bold")
    summary.add_column("Playbook", style="cyan")
    summary.add_column("Pass Rate", style="bold")
    summary.add_column("Fixed / Regressed")
    summary.add_column("Guide Tokens")
    summary.add_column("Gate Decision", style="bold")

    for ep in result.epochs:
        sc = ep.scorecard
        pct = (sc.passed_cases / sc.total_cases * 100.0) if sc.total_cases else 0.0
        rate = f"{sc.passed_cases}/{sc.total_cases} ({pct:.0f}%)"
        if ep.diff is None:
            delta_str = "-"
            dec_style = "dim"
        else:
            delta_str = f"+{len(ep.diff.improvements)} / -{len(ep.diff.regressions)}"
            dec_style = "green" if ep.decision == "PROMOTED" else "red"
        summary.add_row(
            str(ep.epoch),
            ep.version,
            rate,
            delta_str,
            str(ep.playbook_tokens),
            f"[{dec_style}]{ep.decision}[/{dec_style}]",
        )

    console.print(summary)

    for ep in result.epochs:
        if ep.diff is None:
            continue
        if ep.diff.regressions:
            console.print(f"\n[red]Epoch {ep.epoch} ({ep.version}) blocked regressions:[/red] {ep.diff.regressions}")
        if ep.decision == "PROMOTED":
            console.print("")
            console.print(ep.diff.render_rich_table())
            console.print(f"[green]Improvements verified:[/green] {ep.diff.improvements}")

    if result.applied_to_disk:
        console.print(f"\n[bold green]Updated playbook persisted:[/bold green] {PACK_DIR / 'guides' / 'triage_playbook.md'}")
    else:
        console.print("\n[dim]Dry-run complete (playbook untouched). Pass --apply to persist promoted guide, or --reset to restore Day-1 baseline.[/dim]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure and evolve auth0/java-jwt bug triage playbook.")
    parser.add_argument("--live", action="store_true", help="Use real LLM engine instead of deterministic playbook simulator.")
    parser.add_argument("--engine", "-e", default="gemini-2.0-flash", help="Engine binding when using --live.")
    parser.add_argument("--apply", action="store_true", help="Persist promoted playbook to guides/triage_playbook.md.")
    parser.add_argument("--reset", action="store_true", help="Restore guides/triage_playbook.md to Day-1 baseline and exit.")
    args = parser.parse_args()

    if args.reset:
        target = PACK_DIR / "guides" / "triage_playbook.md"
        target.write_text(BASELINE_PLAYBOOK, encoding="utf-8")
        print(f"Restored Day-1 baseline playbook: {target}")
        return

    res = asyncio.run(run_evolution(live=args.live, engine=args.engine, apply=args.apply))
    _render_report(res, live=args.live, engine=args.engine)


if __name__ == "__main__":
    main()
