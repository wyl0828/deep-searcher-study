"""Validate the committed business dataset and evaluation reports."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from evaluation.dataset import load_dataset
from evaluation.metrics import METRIC_VERSION, aggregate

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "evaluation" / "quality_gate.json"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _resolve(project_root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    return candidate.resolve() if candidate.is_absolute() else (project_root / candidate).resolve()


def _nested_value(value: Mapping[str, Any], dotted_path: str) -> Any:
    current: Any = value
    for part in dotted_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise KeyError(dotted_path)
        current = current[part]
    return current


def _threshold_passes(actual: Any, operator: str, expected: Any) -> bool:
    if operator == "eq":
        return actual == expected
    if operator == "ge":
        return actual is not None and actual >= expected
    if operator == "le":
        return actual is not None and actual <= expected
    raise ValueError(f"unsupported threshold operator: {operator}")


def _row_key(row: Mapping[str, Any], group_field: str) -> tuple[str, str]:
    return str(row.get(group_field) or ""), str(row.get("sample_id") or "")


def validate_quality_gate(
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    project_root: Path = ROOT,
    report_overrides: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    """Return a machine-readable validation summary without mutating reports."""
    root = project_root.resolve()
    manifest = _read_json(manifest_path.resolve())
    errors: list[str] = []
    checks: list[dict[str, Any]] = []

    def record(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})
        if not passed:
            errors.append(f"{name}: {detail}")

    manifest_metric_version = str(manifest.get("metric_version") or "")
    record(
        "metric_version",
        manifest_metric_version == METRIC_VERSION,
        f"manifest={manifest_metric_version}, code={METRIC_VERSION}",
    )

    dataset_path = _resolve(root, str(manifest.get("dataset") or ""))
    try:
        dataset = load_dataset(dataset_path)
    except Exception as exc:  # preserve a useful artifact on schema errors
        record("dataset_schema", False, str(exc))
        return {"passed": False, "errors": errors, "checks": checks}

    expected_dataset_version = str(manifest.get("dataset_version") or "")
    record(
        "dataset_version",
        dataset.version == expected_dataset_version,
        f"expected={expected_dataset_version}, actual={dataset.version}",
    )
    record(
        "dataset_sample_count",
        len(dataset.samples) == int(manifest.get("dataset_sample_count") or 0),
        f"actual={len(dataset.samples)}",
    )
    for source in dataset.sources:
        source_path = _resolve(root, source.path)
        actual_hash = (
            hashlib.sha256(source_path.read_bytes()).hexdigest() if source_path.is_file() else None
        )
        record(
            f"source_sha256:{source.document}",
            actual_hash == source.sha256,
            f"expected={source.sha256}, actual={actual_hash}",
        )

    overrides = dict(report_overrides or {})
    report_specs = manifest.get("reports")
    if not isinstance(report_specs, Mapping):
        record("report_manifest", False, "reports must be an object")
        return {"passed": False, "errors": errors, "checks": checks}

    report_summaries: dict[str, Any] = {}
    for report_name, raw_spec in report_specs.items():
        if not isinstance(raw_spec, Mapping):
            record(f"report:{report_name}", False, "report spec must be an object")
            continue
        report_path = overrides.get(str(report_name)) or _resolve(
            root, str(raw_spec.get("path") or "")
        )
        csv_path = report_path.parent / "details.csv"
        if not report_path.is_file() or not csv_path.is_file():
            record(
                f"report_files:{report_name}",
                False,
                f"missing {report_path} or {csv_path}",
            )
            continue
        try:
            report = _read_json(report_path)
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                csv_rows = list(csv.DictReader(handle))
        except Exception as exc:
            record(f"report_read:{report_name}", False, str(exc))
            continue

        details = report.get("details")
        if not isinstance(details, list) or not all(isinstance(row, dict) for row in details):
            record(f"report_details:{report_name}", False, "details must be an object array")
            continue
        expected_rows = int(raw_spec.get("expected_rows") or 0)
        record(
            f"report_rows:{report_name}",
            len(details) == expected_rows and len(csv_rows) == expected_rows,
            f"json={len(details)}, csv={len(csv_rows)}, expected={expected_rows}",
        )
        report_dataset = report.get("dataset")
        report_dataset = report_dataset if isinstance(report_dataset, Mapping) else {}
        record(
            f"report_dataset:{report_name}",
            report_dataset.get("version") == dataset.version
            and report_dataset.get("sha256") == dataset.sha256,
            (f"version={report_dataset.get('version')}, sha256={report_dataset.get('sha256')}"),
        )
        record(
            f"report_metric_version:{report_name}",
            report.get("metric_version") == METRIC_VERSION,
            f"actual={report.get('metric_version')}",
        )
        error_count = sum(bool(row.get("error")) for row in details)
        csv_error_count = sum(bool(row.get("error")) for row in csv_rows)
        record(
            f"report_errors:{report_name}",
            error_count == 0 and csv_error_count == 0,
            f"json={error_count}, csv={csv_error_count}",
        )

        group_field = str(raw_spec.get("group_field") or "")
        json_keys = {_row_key(row, group_field) for row in details}
        csv_keys = {_row_key(row, group_field) for row in csv_rows}
        record(
            f"report_csv_keys:{report_name}",
            len(json_keys) == len(details) and json_keys == csv_keys,
            f"json_keys={len(json_keys)}, csv_keys={len(csv_keys)}",
        )

        required_fields = raw_spec.get("required_detail_fields") or []
        missing_fields = sorted(
            str(field) for field in required_fields if details and str(field) not in details[0]
        )
        record(
            f"report_fields:{report_name}",
            not missing_fields,
            f"missing={missing_fields}",
        )

        grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in details:
            grouped[str(row.get(group_field) or "")].append(row)
        expected_group_rows = raw_spec.get("group_rows") or {}
        actual_group_rows = {name: len(rows) for name, rows in grouped.items()}
        record(
            f"report_groups:{report_name}",
            actual_group_rows == expected_group_rows,
            f"actual={actual_group_rows}, expected={expected_group_rows}",
        )

        recorded_metrics = report.get("metrics")
        recorded_metrics = recorded_metrics if isinstance(recorded_metrics, Mapping) else {}
        aggregate_differences: dict[str, Any] = {}
        for group_name, rows in grouped.items():
            calculated = aggregate(rows)
            recorded = recorded_metrics.get(group_name)
            if not isinstance(recorded, Mapping):
                aggregate_differences[group_name] = "missing metrics"
                continue
            differences = {
                field: {"calculated": value, "recorded": recorded.get(field)}
                for field, value in calculated.items()
                if recorded.get(field) != value
            }
            if differences:
                aggregate_differences[group_name] = differences
        record(
            f"report_aggregates:{report_name}",
            not aggregate_differences,
            f"differences={aggregate_differences}",
        )

        threshold_results: list[dict[str, Any]] = []
        for threshold in raw_spec.get("thresholds") or []:
            dotted_path = str(threshold.get("path") or "")
            operator = str(threshold.get("op") or "")
            expected = threshold.get("value")
            try:
                actual = _nested_value(report, dotted_path)
                passed = _threshold_passes(actual, operator, expected)
                detail = f"actual={actual}, {operator} {expected}"
            except Exception as exc:
                passed = False
                detail = str(exc)
                actual = None
            record(f"threshold:{report_name}:{dotted_path}", passed, detail)
            threshold_results.append(
                {
                    "path": dotted_path,
                    "operator": operator,
                    "expected": expected,
                    "actual": actual,
                    "passed": passed,
                }
            )

        report_summaries[str(report_name)] = {
            "path": str(report_path),
            "rows": len(details),
            "groups": actual_group_rows,
            "thresholds": threshold_results,
        }

    return {
        "passed": not errors,
        "dataset": {
            "path": str(dataset.path),
            "id": dataset.dataset_id,
            "version": dataset.version,
            "sha256": dataset.sha256,
            "sample_count": len(dataset.samples),
        },
        "metric_version": METRIC_VERSION,
        "reports": report_summaries,
        "errors": errors,
        "checks": checks,
    }


def _parse_report_overrides(values: Sequence[str]) -> dict[str, Path]:
    overrides: dict[str, Path] = {}
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not name.strip() or not path.strip():
            raise ValueError("report overrides must use NAME=PATH")
        overrides[name.strip()] = Path(path.strip()).resolve()
    return overrides


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--report-override",
        action="append",
        default=[],
        help="Replace a report path using NAME=PATH; may be repeated.",
    )
    args = parser.parse_args(argv)
    try:
        overrides = _parse_report_overrides(args.report_override)
        summary = validate_quality_gate(
            manifest_path=args.manifest,
            report_overrides=overrides,
        )
    except Exception as exc:
        summary = {"passed": False, "errors": [str(exc)], "checks": []}
    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(args.output)
    print(rendered)
    return 0 if summary.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
