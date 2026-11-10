"""Tool Registry: zero-decorator introspection and Pydantic schema generation for workflow tools."""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import inspect
import json
from pathlib import Path
import typing
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

ToolPhase = Literal["discovery", "execution", "verification"]


class ToolNotFoundError(KeyError):
    """Raised when a requested tool name is not registered."""
    pass


class ToolSpec(BaseModel):
    """Serializable metadata and JSON schema describing an executable tool."""

    name: str
    description: str = ""
    phase: ToolPhase = "execution"
    is_async: bool = False
    required_params: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)


@dataclass
class RegisteredTool:
    """In-memory binding of a ToolSpec, runtime callable, and Pydantic validation model."""

    spec: ToolSpec
    fn: Callable[..., Any]
    args_model: type[BaseModel]


def _is_json_schema_friendly(annotation: Any) -> bool:
    """Return True if an annotation is a standard primitive/typing construct compatible with JSON schema."""
    if annotation is inspect.Parameter.empty or annotation is Any:
        return True
    origin = typing.get_origin(annotation)
    if origin is not None:
        args = typing.get_args(annotation)
        return all(_is_json_schema_friendly(a) for a in args if a is not type(None))
    if annotation in (str, int, float, bool, list, dict, type(None)):
        return True
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return True
    return False


def _build_args_model(tool_name: str, fn: Callable[..., Any]) -> tuple[type[BaseModel], list[str]]:
    """Build a Pydantic args validation model from a Python function's signature."""
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}

    sig = inspect.signature(fn)
    fields: dict[str, Any] = {}
    required_params: list[str] = []

    for param_name, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        ann = hints.get(param_name, param.annotation)
        if ann is inspect.Parameter.empty:
            ann = Any

        # Skip non-JSON-serializable injected dependencies with defaults (e.g. client: AsyncClient | None = None)
        if param.default is not inspect.Parameter.empty and not _is_json_schema_friendly(ann):
            continue

        if param.default is inspect.Parameter.empty:
            fields[param_name] = (ann, ...)
            required_params.append(param_name)
        else:
            fields[param_name] = (ann, param.default)

    model_cls = create_model(
        f"{tool_name}_Args",
        __config__=ConfigDict(arbitrary_types_allowed=True, extra="allow"),
        **fields,
    )
    return model_cls, required_params


class ToolRegistry:
    """Registry storing discoverable tools and generating LLM-ready schemas."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        fn: Callable[..., Any],
        name: str | None = None,
        description: str | None = None,
        phase: ToolPhase = "execution",
        overwrite: bool = False,
    ) -> ToolSpec:
        """Register a sync or async Python function as an executable tool."""
        tool_name = name or fn.__name__
        if tool_name in self._tools and not overwrite:
            raise ValueError(f"Tool '{tool_name}' is already registered.")

        doc = inspect.getdoc(fn) or ""
        desc = description if description is not None else (doc.splitlines()[0] if doc else "")

        args_model, required_params = _build_args_model(tool_name, fn)
        input_schema = args_model.model_json_schema()

        spec = ToolSpec(
            name=tool_name,
            description=desc,
            phase=phase,
            is_async=inspect.iscoroutinefunction(fn),
            required_params=required_params,
            input_schema=input_schema,
        )
        self._tools[tool_name] = RegisteredTool(spec=spec, fn=fn, args_model=args_model)
        return spec

    def load_from_file(
        self,
        file_path: str | Path,
        default_phase: ToolPhase = "execution",
        allowed_names: list[str] | None = None,
    ) -> list[ToolSpec]:
        """Dynamically import a tools.py file and register its top-level public functions."""
        path = Path(file_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Tools module not found at '{path}'")

        module_name = f"airframe_dynamic_tools_{path.parent.name.replace('-', '_')}_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not create module spec for '{path}'")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        loaded_specs: list[ToolSpec] = []
        for attr_name, obj in inspect.getmembers(module, inspect.isfunction):
            if attr_name.startswith("_"):
                continue
            if getattr(obj, "__module__", None) != module.__name__:
                continue
            if allowed_names is not None and attr_name not in allowed_names:
                continue
            tool_spec = self.register(obj, name=attr_name, phase=default_phase, overwrite=True)
            loaded_specs.append(tool_spec)

        return loaded_specs

    def get(self, name: str) -> RegisteredTool:
        """Retrieve a RegisteredTool by name."""
        if name not in self._tools:
            raise ToolNotFoundError(f"Tool '{name}' is not registered.")
        return self._tools[name]

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def list_specs(self, names: list[str] | None = None) -> list[ToolSpec]:
        """Return ToolSpec metadata for all tools or a filtered whitelist."""
        if names is None:
            return [entry.spec for entry in self._tools.values()]
        return [self.get(n).spec for n in names if n in self._tools]

    def to_prompt_schema(self, names: list[str] | None = None) -> str:
        """Render registered tool specifications as compact JSON for model prompts."""
        specs = [
            {
                "name": s.name,
                "description": s.description,
                "parameters": s.input_schema,
            }
            for s in self.list_specs(names)
        ]
        return json.dumps(specs, indent=2)
