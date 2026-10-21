"""Deterministic digest orchestration; only summaries are supplied by a model."""

import json
import subprocess
from pathlib import Path


def worker_command(worker: Path, job_id: str) -> list[str]:
    """Use the installed Hermes runtime without pinning a GC-able Python path.

    The launcher machine boundary is checked before adapting its final module entry
    into a fixed run_path entry. Source data is passed on stdin, never interpolated.
    """
    result = subprocess.run(
        ["hermes", "--print-runtime-command", "--module", "runpy"],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    command = json.loads(result.stdout)
    if (
        not isinstance(command, list)
        or len(command) != 4
        or not all(isinstance(x, str) for x in command)
        or command[1:3] != ["-I", "-c"]
    ):
        raise ValueError("unsupported Hermes runtime command protocol")
    prefix, separator, _ = command[3].rpartition("runpy.run_module(")
    if not separator or "import hermes_bootstrap" not in prefix:
        raise ValueError("unsupported Hermes bootstrap entry")
    command[3] = (
        prefix + "worker = sys.argv.pop(1); runpy.run_path(worker, run_name='__main__')"
    )
    return command + [str(worker), "--job-id", job_id]


def summarize_manifest(manifest: dict, job_id: str) -> dict:
    """One bounded, tool-less call; do not let the model supply structural fields."""
    if not manifest["entries"]:
        return {"summaries": {}, "model": None, "provider": None}
    entries = []
    for item in manifest["entries"]:
        card = item.get("candidate") or {}
        entries.append(
            {
                "id": item["id"],
                "title": item["title"][:300],
                "summary": item.get("summary", "")[:500],
                "source_clues": [
                    e.get("excerpt", "")[:280] for e in card.get("evidence", [])[:2]
                ],
            }
        )
    worker = Path(__file__).resolve().parents[2] / "scripts/digest_summarize.py"
    result = subprocess.run(
        worker_command(worker, job_id),
        input=json.dumps({"entries": entries}, ensure_ascii=False),
        capture_output=True,
        text=True,
        check=False,
        timeout=130,
    )
    if result.stderr:
        import sys

        print(result.stderr, file=sys.stderr, end="")
    result.check_returncode()
    response = json.loads(result.stdout)
    if not isinstance(response, dict) or set(response) != {
        "summaries",
        "model",
        "provider",
    }:
        raise ValueError("summary worker returned invalid fields")
    return response


def run_digest(
    collector_dir: Path, candidate_dir: Path, output_dir: Path, job_id: str, *, now=None
) -> tuple[str, Path]:
    """The delivery boundary sees text only after every structural check passes."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from crawler.digest import build_manifest, render_digest, validate_digest
    from crawler.discovery import _atomic_write

    clock = now or datetime.now(ZoneInfo("Asia/Taipei"))
    manifest = build_manifest(collector_dir, candidate_dir, clock)
    drafts = summarize_manifest(manifest, job_id)
    text = render_digest(manifest, drafts["summaries"])
    validate_digest(text, manifest)
    destination = output_dir / clock.strftime("%Y-%m-%d_%H%M%S_%f")
    for name, value in (
        ("manifest.json", manifest),
        ("summaries.json", drafts),
        (
            "validation.json",
            {
                "validated": True,
                "entries": len(manifest["entries"]),
                "unique_urls": len({e["url"] for e in manifest["entries"]}),
            },
        ),
    ):
        _atomic_write(
            destination / name, json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        )
    _atomic_write(destination / "report.md", text)
    return text, destination


def main() -> None:
    import argparse
    import os
    import sys

    root = Path(__file__).resolve().parents[2]
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    parser = argparse.ArgumentParser(description="經過程式品質驗證的新聞匯總")
    parser.add_argument("--job-id", required=True)
    parser.add_argument(
        "--collector-dir", type=Path, default=home / "cron/output/90a0d20fbc91"
    )
    parser.add_argument("--candidate-dir", type=Path, default=root / "data/deep-dive")
    parser.add_argument("--output-dir", type=Path, default=root / "data/digests")
    args = parser.parse_args()
    try:
        text, destination = run_digest(
            args.collector_dir, args.candidate_dir, args.output_dir, args.job_id
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(
            f"匯總品質流程失敗，未輸出通知清單：{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    print(f"匯總品質驗證通過；產物：{destination}", file=sys.stderr)
    print(text, end="", flush=True)


if __name__ == "__main__":
    main()
