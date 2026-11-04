"""Canonical prompt profiler: compiles task intent into provider-optimal prompt syntax."""

from __future__ import annotations

from typing import Any

from jinja2 import Environment, select_autoescape

CLAUDE_TEMPLATE = """{% if system_prompt %}<instructions>
{{ system_prompt }}
</instructions>

{% endif %}{% if context %}<context>
{{ context }}
</context>

{% endif %}<task>
{{ prompt }}
</task>"""

GEMINI_TEMPLATE = """{% if system_prompt %}[SYSTEM DIRECTIVE]
{{ system_prompt }}

{% endif %}{% if context %}[BACKGROUND CONTEXT]
{{ context }}

{% endif %}[TASK INSTRUCTION]
{{ prompt }}"""

DEFAULT_TEMPLATE = """{% if system_prompt %}{{ system_prompt }}

{% endif %}{% if context %}Context:
{{ context }}

{% endif %}{{ prompt }}"""


class PromptProfiler:
    """Compiles canonical prompt elements into model-preferred syntax representations."""

    def __init__(self) -> None:
        self.env = Environment(autoescape=select_autoescape([]))
        self.templates = {
            "anthropic": self.env.from_string(CLAUDE_TEMPLATE),
            "gemini": self.env.from_string(GEMINI_TEMPLATE),
            "default": self.env.from_string(DEFAULT_TEMPLATE),
        }

    def compile(
        self,
        provider: str,
        prompt: str,
        system_prompt: str | None = None,
        context: str | None = None,
        **extra_vars: Any,
    ) -> str:
        """Compile prompt variables into the optimal syntax for the given provider."""
        template = self.templates.get(provider.lower(), self.templates["default"])
        return template.render(
            prompt=prompt,
            system_prompt=system_prompt,
            context=context,
            **extra_vars,
        ).strip()
