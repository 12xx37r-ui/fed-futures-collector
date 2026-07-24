
from __future__ import annotations

import ast
import re
import shutil
from datetime import datetime
from pathlib import Path

VERSION_OLD = "3.1.0-fred-date-parser-parallel"
VERSION_NEW = "3.3.0-fred-official-api"

def find_repo(start: Path) -> Path:
    for candidate in [start, *start.parents, Path.cwd()]:
        if (candidate / "collector.py").is_file() and (candidate / "engine" / "run_engine.py").is_file():
            return candidate.resolve()
    common = Path.home() / "OneDrive" / "temp" / "GitHub" / "fed-futures-collector"
    if (common / "collector.py").is_file():
        return common.resolve()
    raise FileNotFoundError("Put this patch folder inside fed-futures-collector and run again.")

def backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = path.with_name(path.name + f".backup-{stamp}")
    shutil.copy2(path, target)
    return target

def replace_function(source: str, name: str, replacement: str) -> str:
    pattern = re.compile(rf"^def {re.escape(name)}\(.*?(?=^def |\Z)", re.MULTILINE | re.DOTALL)
    match = pattern.search(source)
    if not match:
        raise RuntimeError(f"Function not found: {name}")
    return source[:match.start()] + replacement.rstrip() + "\n\n" + source[match.end():]

FRED_FUNCTION = """
def fred_series_csv(series_id: str) -> dict[str, Any]:
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("FRED_API_KEY is missing from GitHub Actions Secrets.")

    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "asc",
    }

    with make_session(total_retries=1) as session:
        response = session.get(
            url,
            params=params,
            timeout=(5, 20),
            allow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()

    if payload.get("error_message"):
        raise RuntimeError(f"FRED API error: {payload['error_message']}")

    rows = []
    for item in payload.get("observations") or []:
        day = str(item.get("date") or "").strip()
        raw_value = str(item.get("value") or "").strip()
        if not day or raw_value in {"", ".", "NA", "NaN"}:
            continue
        try:
            rows.append({"date": day, "value": float(raw_value)})
        except ValueError:
            continue

    if not rows:
        raise ValueError(f"FRED API returned no numeric observations for {series_id}")

    return {
        "series_id": series_id,
        "latest": rows[-1],
        "observations": rows[-900:],
        "source_url": response.url,
        "stale": False,
    }
"""

def patch_collector(repo: Path) -> None:
    path = repo / "collector.py"
    source = path.read_text(encoding="utf-8")
    if VERSION_NEW in source and "api.stlouisfed.org/fred/series/observations" in source:
        print("[OK] collector.py already V3.3")
        return
    if "import os" not in source:
        source = source.replace("import json\n", "import json\nimport os\n", 1)
    source = replace_function(source, "fred_series_csv", FRED_FUNCTION)
    source = source.replace(
        'log(f"[4/6] FRED parallel requests: {len(ids)} series")',
        'log(f"[4/6] FRED official API requests: {len(ids)} series")',
    )
    source = source.replace(VERSION_OLD, VERSION_NEW)
    source = source.replace("3.2.0-fred-timeout-controlled-retry", VERSION_NEW)
    ast.parse(source, filename=str(path))
    print(f"[BACKUP] {backup(path)}")
    path.write_text(source, encoding="utf-8")
    print(f"[OK] patched {path}")

def patch_engine(repo: Path) -> None:
    path = repo / "engine" / "run_engine.py"
    source = path.read_text(encoding="utf-8")
    changed = source.replace(VERSION_OLD, VERSION_NEW)
    changed = changed.replace("3.2.0-fred-timeout-controlled-retry", VERSION_NEW)
    if changed == source:
        if VERSION_NEW in source:
            print("[OK] run_engine.py already V3.3")
            return
        raise RuntimeError("Engine version string not found.")
    ast.parse(changed, filename=str(path))
    print(f"[BACKUP] {backup(path)}")
    path.write_text(changed, encoding="utf-8")
    print(f"[OK] patched {path}")

def patch_workflow(repo: Path) -> None:
    path = repo / ".github" / "workflows" / "update-data.yml"
    source = path.read_text(encoding="utf-8")
    changed = source.replace("RUNNING FED ENGINE V3.2", "RUNNING FED ENGINE V3.3")
    changed = changed.replace("RUNNING FED ENGINE V3.1", "RUNNING FED ENGINE V3.3")
    if "FRED_API_KEY:" not in changed:
        marker = '          PYTHONUNBUFFERED: "1"\n'
        if marker not in changed:
            raise RuntimeError("Workflow env block not found.")
        changed = changed.replace(
            marker,
            marker + "          FRED_API_KEY: ${{ secrets.FRED_API_KEY }}\n",
            1,
        )
    if changed == source:
        print("[OK] workflow already configured")
        return
    print(f"[BACKUP] {backup(path)}")
    path.write_text(changed, encoding="utf-8")
    print(f"[OK] patched {path}")

def main() -> int:
    try:
        repo = find_repo(Path(__file__).resolve().parent)
        print(f"[REPO] {repo}")
        patch_collector(repo)
        patch_engine(repo)
        patch_workflow(repo)
        print()
        print("V3.3 patch completed.")
        print("Summary: Use official FRED API V3.3")
        print("Then Commit to main and Push origin.")
        return 0
    except Exception as exc:
        print(f"[FAILED] {type(exc).__name__}: {exc}")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
