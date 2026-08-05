from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BLOCKING_SEVERITIES = {"HIGH", "CRITICAL"}


def _escape_command_data(value: object) -> str:
    return (
        str(value)
        .replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
    )


def _emit_error(title: str, message: str) -> None:
    print(
        f"::error title={_escape_command_data(title)}::"
        f"{_escape_command_data(message)}"
    )


def _configuration_findings(report: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []

    for result in report.get("Results") or []:
        target = str(result.get("Target") or "<unknown>")

        for item in result.get("Misconfigurations") or []:
            severity = str(item.get("Severity") or "").upper()

            if severity not in BLOCKING_SEVERITIES:
                continue

            findings.append(
                {
                    "severity": severity,
                    "identifier": str(item.get("ID") or "<unknown>"),
                    "target": target,
                    "title": str(item.get("Title") or ""),
                    "resolution": str(item.get("Resolution") or ""),
                }
            )

    return findings


def _image_findings(report: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []

    for result in report.get("Results") or []:
        target = str(result.get("Target") or "<unknown>")

        for item in result.get("Vulnerabilities") or []:
            severity = str(item.get("Severity") or "").upper()

            if severity not in BLOCKING_SEVERITIES:
                continue

            findings.append(
                {
                    "severity": severity,
                    "identifier": str(
                        item.get("VulnerabilityID") or "<unknown>"
                    ),
                    "target": target,
                    "package": str(item.get("PkgName") or "<unknown>"),
                    "installed": str(item.get("InstalledVersion") or ""),
                    "fixed": str(item.get("FixedVersion") or ""),
                    "title": str(item.get("Title") or ""),
                }
            )

    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--kind",
        required=True,
        choices=("config", "image"),
    )
    parser.add_argument(
        "--report",
        required=True,
        type=Path,
    )
    args = parser.parse_args()

    if not args.report.is_file():
        _emit_error(
            "Trivy report missing",
            f"Expected report was not created: {args.report}",
        )
        return 2

    try:
        report = json.loads(args.report.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        _emit_error(
            "Trivy report invalid",
            f"Could not read {args.report}: {exc}",
        )
        return 2

    if args.kind == "config":
        findings = _configuration_findings(report)
    else:
        findings = _image_findings(report)

    print(f"TRIVY_REPORT_KIND={args.kind}")
    print(f"TRIVY_BLOCKING_FINDINGS={len(findings)}")

    for finding in findings:
        identifier = finding["identifier"]
        severity = finding["severity"]

        if args.kind == "config":
            message = (
                f"severity={severity}; "
                f"target={finding['target']}; "
                f"id={identifier}; "
                f"title={finding['title']}; "
                f"resolution={finding['resolution']}"
            )
        else:
            message = (
                f"severity={severity}; "
                f"target={finding['target']}; "
                f"vulnerability={identifier}; "
                f"package={finding['package']}; "
                f"installed={finding['installed']}; "
                f"fixed={finding['fixed']}; "
                f"title={finding['title']}"
            )

        _emit_error(
            f"Trivy {args.kind} {identifier}",
            message,
        )

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())