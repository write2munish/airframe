"""Differential regression engine: compares candidate models against golden baselines."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from rich.table import Table


class CaseResult(BaseModel):
    """Execution and verification outcome for an individual test case."""

    case_id: str
    passed: bool
    latency_ms: float = 0.0
    tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None


class RunScorecard(BaseModel):
    """Aggregate benchmark results for a model run across a testbed."""

    model_name: str
    total_cases: int
    passed_cases: int
    failed_cases: int
    avg_latency_ms: float
    total_tokens: int
    results: list[CaseResult] = Field(default_factory=list)

    @classmethod
    def from_results(cls, model_name: str, results: list[CaseResult]) -> RunScorecard:
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        failed = total - passed
        avg_lat = sum(r.latency_ms for r in results) / total if total > 0 else 0.0
        tot_tok = sum(r.tokens for r in results)

        return cls(
            model_name=model_name,
            total_cases=total,
            passed_cases=passed,
            failed_cases=failed,
            avg_latency_ms=round(avg_lat, 2),
            total_tokens=tot_tok,
            results=results,
        )


class DifferentialReport(BaseModel):
    """Comparative analysis highlighting regressions, improvements, and performance drift."""

    baseline: RunScorecard
    candidate: RunScorecard
    regressions: list[str] = Field(
        default_factory=list, description="Cases passed by baseline but failed by candidate."
    )
    improvements: list[str] = Field(
        default_factory=list, description="Cases failed by baseline but passed by candidate."
    )
    latency_delta_ms: float = Field(
        description="Candidate latency minus baseline latency (negative means faster)."
    )
    token_delta: int = Field(
        description="Candidate tokens minus baseline tokens (negative means fewer tokens)."
    )

    @property
    def has_regressions(self) -> bool:
        return len(self.regressions) > 0

    def render_markdown(self) -> str:
        """Render a clean Markdown differential report."""
        status_emoji = "❌" if self.has_regressions else "✅"
        lines = [
            f"# {status_emoji} Airframe Differential Regression Report",
            f"\n**Baseline Model:** `{self.baseline.model_name}`",
            f"**Candidate Model:** `{self.candidate.model_name}`\n",
            "| Metric | Baseline | Candidate | Delta |",
            "| :--- | :--- | :--- | :--- |",
            f"| **Pass Rate** | {self.baseline.passed_cases}/{self.baseline.total_cases} | {self.candidate.passed_cases}/{self.candidate.total_cases} | {self.candidate.passed_cases - self.baseline.passed_cases:+d} |",
            f"| **Avg Latency** | {self.baseline.avg_latency_ms:.1f}ms | {self.candidate.avg_latency_ms:.1f}ms | {self.latency_delta_ms:+.1f}ms |",
            f"| **Total Tokens** | {self.baseline.total_tokens:,} | {self.candidate.total_tokens:,} | {self.token_delta:+,d} |",
        ]

        if self.regressions:
            lines.append("\n### 🚨 Regressions (Passed in Baseline, Failed in Candidate):")
            for case in self.regressions:
                lines.append(f"- 🔴 `{case}`")

        if self.improvements:
            lines.append("\n### 🎉 Improvements (Failed in Baseline, Passed in Candidate):")
            for case in self.improvements:
                lines.append(f"- 🟢 `{case}`")

        return "\n".join(lines)

    def render_rich_table(self) -> Table:
        """Render a formatted Rich Table for CLI display."""
        table = Table(title=f"Differential Regression Analysis: {self.baseline.model_name} vs {self.candidate.model_name}")
        table.add_column("Metric", style="bold cyan")
        table.add_column(f"Baseline ({self.baseline.model_name})", style="magenta")
        table.add_column(f"Candidate ({self.candidate.model_name})", style="blue")
        table.add_column("Delta / Impact", style="bold")

        # Pass rate
        p_delta = self.candidate.passed_cases - self.baseline.passed_cases
        p_delta_str = f"[green]+{p_delta}[/green]" if p_delta > 0 else (f"[red]{p_delta}[/red]" if p_delta < 0 else "0")
        table.add_row(
            "Pass Rate",
            f"{self.baseline.passed_cases}/{self.baseline.total_cases}",
            f"{self.candidate.passed_cases}/{self.candidate.total_cases}",
            p_delta_str,
        )

        # Latency
        lat_style = "green" if self.latency_delta_ms < 0 else "yellow"
        table.add_row(
            "Avg Latency",
            f"{self.baseline.avg_latency_ms:.1f} ms",
            f"{self.candidate.avg_latency_ms:.1f} ms",
            f"[{lat_style}]{self.latency_delta_ms:+.1f} ms[/{lat_style}]",
        )

        # Regressions summary
        reg_style = "bold red" if self.regressions else "green"
        reg_str = f"[{reg_style}]{len(self.regressions)} Regressions[/{reg_style}]"
        table.add_row("Regressions", "-", "-", reg_str)

        return table


def compare_scorecards(baseline: RunScorecard, candidate: RunScorecard) -> DifferentialReport:
    """Generate a differential report comparing two model runs across the same test cases."""
    base_map = {r.case_id: r for r in baseline.results}
    cand_map = {r.case_id: r for r in candidate.results}

    regressions: list[str] = []
    improvements: list[str] = []

    for case_id, base_res in base_map.items():
        cand_res = cand_map.get(case_id)
        if not cand_res:
            continue

        if base_res.passed and not cand_res.passed:
            regressions.append(case_id)
        elif not base_res.passed and cand_res.passed:
            improvements.append(case_id)

    lat_delta = round(candidate.avg_latency_ms - baseline.avg_latency_ms, 2)
    tok_delta = candidate.total_tokens - baseline.total_tokens

    return DifferentialReport(
        baseline=baseline,
        candidate=candidate,
        regressions=regressions,
        improvements=improvements,
        latency_delta_ms=lat_delta,
        token_delta=tok_delta,
    )
