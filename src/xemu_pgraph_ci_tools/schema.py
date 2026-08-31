# ruff: noqa: T201, PLC0415

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import types
import typing
from typing import Any


def generate_json_schema(tp: Any, *, is_top_level: bool = True) -> dict[str, Any]:
    """Generates a JSON Schema dict via introspection for a given model class or type."""
    if tp is int:
        return {"type": "integer"}
    if tp is float:
        return {"type": "number"}
    if tp is str:
        return {"type": "string"}
    if tp is bool:
        return {"type": "boolean"}
    if tp is type(None) or tp is None:
        return {"type": "null"}
    if tp is typing.Any:
        return {}

    origin = typing.get_origin(tp)
    args = typing.get_args(tp)

    if origin is typing.Union or isinstance(tp, types.UnionType):
        non_none = [a for a in args if a not in (type(None), None)]
        has_none = len(non_none) < len(args)
        if len(non_none) == 1:
            base = generate_json_schema(non_none[0], is_top_level=False)
            if has_none:
                return {"anyOf": [base, {"type": "null"}]}
            return base
        schemas = [generate_json_schema(a, is_top_level=False) for a in non_none]
        if has_none:
            schemas.append({"type": "null"})
        return {"anyOf": schemas}

    if origin in (list, set, tuple):
        item_schema = generate_json_schema(args[0], is_top_level=False) if args else {}
        res: dict[str, Any] = {"type": "array", "items": item_schema}
        if origin is set:
            res["uniqueItems"] = True
        return res

    if origin in (dict,):
        val_schema = (
            generate_json_schema(args[1], is_top_level=False) if len(args) > 1 else {}
        )
        return {"type": "object", "additionalProperties": val_schema}

    if dataclasses.is_dataclass(tp):
        type_hints = (
            typing.get_type_hints(tp)
            if isinstance(tp, type)
            else typing.get_type_hints(type(tp))
        )
        props: dict[str, Any] = {}
        req: list[str] = []
        for f in dataclasses.fields(tp):
            field_type = type_hints.get(f.name, f.type)
            props[f.name] = generate_json_schema(field_type, is_top_level=False)
            if (
                f.default is dataclasses.MISSING
                and f.default_factory is dataclasses.MISSING
            ):
                req.append(f.name)

        doc = tp.__doc__.strip() if tp.__doc__ else None
        title = tp.__name__ if isinstance(tp, type) else type(tp).__name__
        res_obj: dict[str, Any] = {}
        if is_top_level:
            res_obj["$schema"] = "http://json-schema.org/draft-07/schema#"
            res_obj["title"] = title
            if doc:
                res_obj["description"] = doc
            res_obj["type"] = "object"
            res_obj["properties"] = props
        else:
            res_obj["title"] = title
            if doc:
                res_obj["description"] = doc
            res_obj["type"] = "object"
            res_obj["properties"] = props

        if req:
            res_obj["required"] = req
        return res_obj

    return {}


def emit_json_schema(tp: Any) -> str:
    """Emits formatted JSON Schema string for a given model class or type."""
    schema_dict = generate_json_schema(tp, is_top_level=True)
    return json.dumps(schema_dict, indent=2)


def main(argv: list[str] | None = None) -> int:
    from xemu_pgraph_ci_tools import models

    parser = argparse.ArgumentParser(description="Emit JSON schema for models.")
    parser.add_argument(
        "model",
        nargs="?",
        default="pipeline",
        help="Model name (e.g. pipeline, comparison, comparisons_map, runner, renderer, results, or full class name)",
    )
    parser.add_argument(
        "--model",
        "-m",
        dest="model_option",
        default=None,
        help="Model name option",
    )

    args = parser.parse_args(argv)
    target_model = args.model_option or args.model

    model_map: dict[str, Any] = {
        "pipeline": models.PipelineReport,
        "pipelinereport": models.PipelineReport,
        "comparison": models.ComparisonSummary,
        "comparisonsummary": models.ComparisonSummary,
        "comparisons_map": getattr(models, "ComparisonsMap", models.ComparisonSummary),
        "comparisonsmap": getattr(models, "ComparisonsMap", models.ComparisonSummary),
        "renderer": getattr(models, "RendererInfo", dict),
        "rendererinfo": getattr(models, "RendererInfo", dict),
        "runner": getattr(models, "RunnerInfo", dict),
        "runnerinfo": getattr(models, "RunnerInfo", dict),
        "results": getattr(models, "TestResultsManifest", dict),
        "testresultsmanifest": getattr(models, "TestResultsManifest", dict),
        "resultsinfo": models.ResultsInfo,
    }

    key = target_model.lower()
    if key in model_map:
        target_cls = model_map[key]
    elif hasattr(models, target_model):
        target_cls = getattr(models, target_model)
    else:
        sys.stderr.write(
            f"Unknown model '{target_model}'. Available models: {', '.join(sorted(model_map.keys()))}\n"
        )
        return 1

    print(emit_json_schema(target_cls))
    return 0


if __name__ == "__main__":
    sys.exit(main())
