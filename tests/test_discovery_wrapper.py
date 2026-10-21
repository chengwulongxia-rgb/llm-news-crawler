"""Exercise the actual cron wrapper without network or touching the real dedup DB."""

import json
import os
import subprocess
from pathlib import Path


def test_collector_wrapper_enables_dated_discovery_reports(tmp_path):
    home = tmp_path / "home"
    project = home / "projects" / "llm-news-crawler"
    project.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.jsonl"
    uv = bin_dir / "uv"
    uv.write_text(
        '#!/usr/bin/env python3\nimport json,os,sys\nwith open(os.environ["CALL_LOG"],"a") as f:f.write(json.dumps(sys.argv[1:])+"\\n")\n'
    )
    uv.chmod(0o755)
    script = Path(__file__).resolve().parents[1] / "scripts" / "llm-news-crawler.sh"
    result = subprocess.run(
        ["bash", str(script)],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "HOME": str(home),
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            "CALL_LOG": str(log),
        },
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    args = calls[-1]
    report = Path(args[args.index("--deep-dive-output") + 1])
    assert report.parent == project / "data" / "deep-dive"
    assert report.suffix == ".json" and len(report.stem) >= 17
    assert args[args.index("--deep-dive-budget") + 1] == "12"
    assert args[args.index("--limit") + 1] == "20"
