"""Single-Tool Calculator execution script using Airframe Agent Operating System."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "workflows" / "calculator"))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from airframe.adapters import get_adapter
from airframe.guards import ExecutableGuard, PayloadBounds
from airframe.isolation import ContextPartitioner
from airframe.sensors import SensorPipeline, ToolCallValidatorSensor
from guards.contracts import CompoundInterestContract
from sensors.deterministic import CompoundInterestMathSensor
from tools.calculator import calculate_compound_interest

console = Console()


async def run_single_tool_calculator(prompt: str) -> None:
    console.print(Panel(f"[bold cyan]User Request:[/bold cyan] {prompt}", title="✈️ Airframe Single-Tool Calculator"))

    # Step 1: Initialize Context Partitioner (Anti-Pollution)
    partitioner = ContextPartitioner(agent_id="calculator_worker")
    partitioner.log_turn(role="user", content=prompt, tokens=35)
    console.print("[dim]1. Context Partitioner initialized (private transcript: transcript.jsonl)[/dim]")

    # Step 2: Simulate/Execute Agent Tool Decision
    console.print("[bold yellow]2. Agent decides to invoke computational tool...[/bold yellow]")
    tool_call = {
        "tool_name": "calculate_compound_interest",
        "arguments": {
            "principal": 10000.0,
            "rate_percent": 5.0,
            "time_years": 3.0,
            "compounding_frequency": 1,
        },
    }

    # Step 3: Run Tier 2 Sensor: Tool Call Validator
    tool_validator = ToolCallValidatorSensor(
        allowed_tools=["calculate_compound_interest"],
        required_params={"calculate_compound_interest": ["principal", "rate_percent", "time_years"]},
    )
    val_res = await tool_validator.evaluate(tool_call)
    assert val_res.passed, f"Tool call validation failed: {val_res.message}"
    console.print(f"   [green]✓ Tier 2 Sensor passed:[/green] {val_res.message}")

    # Step 4: Execute Authorized Tool
    tool_result = calculate_compound_interest(**tool_call["arguments"])
    console.print(f"   [cyan]Tool Output:[/cyan] {tool_result}")
    partitioner.log_turn(
        role="tool",
        content=str(tool_result),
        tokens=120,
        metadata={"tool": "calculate_compound_interest"},
    )

    # Step 5: Format Output & Enforce Executable Guard Contract
    console.print("[bold yellow]3. Executing Executable Guard on output schema...[/bold yellow]")
    guard = ExecutableGuard(
        schema=CompoundInterestContract,
        bounds=PayloadBounds(max_bytes=4096, forbidden_substrings=["eval("]),
    )

    # Structured payload produced by agent
    raw_agent_json = f"""
    {{
        "currency": "₹",
        "principal": {tool_result['principal']},
        "rate_percent": {tool_result['rate_percent']},
        "time_years": {tool_result['time_years']},
        "compounding_frequency": {tool_result['compounding_frequency']},
        "total_amount": {tool_result['total_amount']},
        "total_interest": {tool_result['total_interest']},
        "explanation": "With an initial principal of ₹10,000 at an annual interest rate of 5% compounded annually for 3 years, the total accumulated amount is ₹11,576.25, yielding a total compound interest of ₹1,576.25."
    }}
    """

    contract_result = guard.validate(raw_agent_json)
    console.print(f"   [green]✓ Executable Guard passed:[/green] Contract '{CompoundInterestContract.__name__}' validated.")

    # Step 6: Multi-Tier Sensor Pipeline (Tier 1 Math Sensor)
    console.print("[bold yellow]4. Running Multi-Tier Sensor Verification Pipeline...[/bold yellow]")
    pipeline = SensorPipeline([CompoundInterestMathSensor()])
    sensor_results = await pipeline.run(contract_result)

    for s_res in sensor_results:
        status_color = "green" if s_res.passed else "red"
        console.print(f"   [{status_color}]✓ Tier {s_res.tier} {s_res.sensor_name}:[/{status_color}] {s_res.message}")

    # Step 7: Compress Handoff to Supervisor (Shielding intermediate tokens)
    handoff = partitioner.compress_handoff(
        final_output=contract_result.explanation,
        status="SUCCESS",
        final_output_tokens=45,
    )

    # Display Summary Table
    table = Table(title="Financial Breakdown", show_header=True, header_style="bold magenta")
    table.add_column("Parameter", style="cyan")
    table.add_column("Value", style="bold green")

    table.add_row("Principal (P)", f"{contract_result.currency}{contract_result.principal:,.2f}")
    table.add_row("Annual Interest Rate (r)", f"{contract_result.rate_percent}%")
    table.add_row("Time Duration (t)", f"{contract_result.time_years:.0f} Years")
    table.add_row("Compounding Frequency (n)", "Annually (1/yr)")
    table.add_row("Compound Interest Earned", f"{contract_result.currency}{contract_result.total_interest:,.2f}")
    table.add_row("Total Accumulated Amount", f"{contract_result.currency}{contract_result.total_amount:,.2f}")

    console.print("\n", table)

    # Display Telemetry & Handoff Shielding
    telemetry_table = Table(title="Airframe Telemetry & Token Shielding", show_header=True, header_style="bold blue")
    table_headers = ["Metric", "Value"]
    telemetry_table.add_column("Metric", style="dim")
    telemetry_table.add_column("Value", style="bold")
    telemetry_table.add_row("Execution Status", f"[green]{handoff.status}[/green]")
    telemetry_table.add_row("Total Steps Recorded", str(handoff.total_steps))
    telemetry_table.add_row("Intermediate Tokens Shielded from Parent Context", f"[bold cyan]{handoff.tokens_discarded:,} tokens[/bold cyan]")
    telemetry_table.add_row("Final Contract Payload", f"{len(raw_agent_json.encode('utf-8'))} bytes")

    console.print("\n", telemetry_table)


if __name__ == "__main__":
    query = "Calculate the compound interest on ₹10,000 at 5% for 3 years"
    asyncio.run(run_single_tool_calculator(query))
