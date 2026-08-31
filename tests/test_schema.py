from __future__ import annotations

import dataclasses
import io
import json
import sys
import unittest
from unittest.mock import patch

from xemu_pgraph_ci_tools.comparator import main as comparator_main
from xemu_pgraph_ci_tools.hw_diffs import main as hw_diffs_main
from xemu_pgraph_ci_tools.models import (
    ComparisonsMap,
    ComparisonSummary,
    Difference,
    KnownIssue,
    KnownIssueFilter,
    PipelineReport,
    RendererInfo,
    ResultsInfo,
    RunnerInfo,
    TestResultItem,
    TestResultsManifest,
)
from xemu_pgraph_ci_tools.pipeline import main as pipeline_main
from xemu_pgraph_ci_tools.runner import main as runner_main
from xemu_pgraph_ci_tools.runner import merge_main
from xemu_pgraph_ci_tools.schema import emit_json_schema, generate_json_schema
from xemu_pgraph_ci_tools.schema import main as schema_main
from xemu_pgraph_ci_tools.xemu_diffs import main as xemu_diffs_main


class TestSchemaGeneration(unittest.TestCase):
    def test_dynamic_introspection_updates_schema(self):
        @dataclasses.dataclass
        class DynamicModel:
            """Test dynamic model."""

            field_a: str
            field_b: int = 10

        schema = generate_json_schema(DynamicModel)
        assert schema["title"] == "DynamicModel"
        assert "field_a" in schema["properties"]
        assert "field_b" in schema["properties"]
        assert schema["required"] == ["field_a"]

        # Dynamically define new model class with an extra field
        @dataclasses.dataclass
        class DynamicModelExtended(DynamicModel):
            field_c: float = 3.14

        schema_ext = generate_json_schema(DynamicModelExtended)
        assert "field_c" in schema_ext["properties"]
        assert schema_ext["properties"]["field_c"] == {"type": "number"}

    def test_all_models_have_valid_schemas(self):
        models_to_check = [
            PipelineReport,
            ComparisonSummary,
            ResultsInfo,
            ComparisonsMap,
            RendererInfo,
            RunnerInfo,
            TestResultsManifest,
            TestResultItem,
            Difference,
            KnownIssue,
            KnownIssueFilter,
        ]
        for model in models_to_check:
            schema = generate_json_schema(model)
            assert schema["$schema"] == "http://json-schema.org/draft-07/schema#"
            assert schema["title"] == model.__name__
            assert schema["type"] == "object"
            assert "properties" in schema

            # Check model get_json_schema classmethod
            method_schema = model.get_json_schema()
            assert method_schema == schema

    def test_emit_json_schema_output(self):
        json_str = emit_json_schema(ComparisonSummary)
        data = json.loads(json_str)
        assert data["title"] == "ComparisonSummary"

    def test_cli_emit_schema_pipeline(self):
        out = io.StringIO()
        with (
            patch.object(sys, "argv", ["xemu-pgraph-pipeline", "--emit-schema"]),
            patch("sys.stdout", out),
        ):
            ret = pipeline_main()
            assert ret == 0
        data = json.loads(out.getvalue())
        assert data["title"] == "PipelineReport"

    def test_cli_emit_schema_comparator(self):
        out = io.StringIO()
        with (
            patch.object(sys, "argv", ["xemu-pgraph-compare", "--schema"]),
            patch("sys.stdout", out),
        ):
            ret = comparator_main()
            assert ret == 0
        data = json.loads(out.getvalue())
        assert data["title"] == "ComparisonSummary"

    def test_cli_emit_schema_hw_diffs(self):
        out = io.StringIO()
        with (
            patch.object(sys, "argv", ["xemu-pgraph-hw-diff", "--emit-schema"]),
            patch("sys.stdout", out),
        ):
            ret = hw_diffs_main()
            assert ret == 0
        data = json.loads(out.getvalue())
        assert data["title"] == "ComparisonSummary"

    def test_cli_emit_schema_xemu_diffs(self):
        out = io.StringIO()
        with (
            patch.object(sys, "argv", ["xemu-pgraph-xemu-diff", "--emit-schema"]),
            patch("sys.stdout", out),
        ):
            ret = xemu_diffs_main()
            assert ret == 0
        data = json.loads(out.getvalue())
        assert data["title"] == "ComparisonsMap"

    def test_cli_emit_schema_runner(self):
        out = io.StringIO()
        with (
            patch.object(sys, "argv", ["xemu-pgraph-run", "--emit-schema"]),
            patch("sys.stdout", out),
        ):
            ret = runner_main()
            assert ret == 0
        data = json.loads(out.getvalue())
        assert data["title"] == "TestResultsManifest"

    def test_cli_emit_schema_merge(self):
        out = io.StringIO()
        with (
            patch.object(sys, "argv", ["xemu-pgraph-merge", "--schema"]),
            patch("sys.stdout", out),
        ):
            ret = merge_main()
            assert ret == 0
        data = json.loads(out.getvalue())
        assert data["title"] == "TestResultsManifest"

    def test_standalone_schema_cli(self):
        out = io.StringIO()
        with (
            patch.object(sys, "argv", ["xemu-pgraph-schema", "comparison"]),
            patch("sys.stdout", out),
        ):
            ret = schema_main()
            assert ret == 0
        data = json.loads(out.getvalue())
        assert data["title"] == "ComparisonSummary"


if __name__ == "__main__":
    unittest.main()
