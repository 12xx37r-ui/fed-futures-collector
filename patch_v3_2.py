from __future__ import annotations

import ast
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path


OLD_FRED_FUNCTION = r'''def fred_series_csv(series_id: str) -> dict[str, Any]:
    # The graph download endpoint is public and does not require an API key.
    # One series per request is intentional: fredgraph.csv does not provide a
    # reliable comma-separated bulk contract for unattended GitHub runners.
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={quote(series_id)}"
    response = request(url, official=True, timeout=(4, 10), retries=2)
    return parse_fred_series_csv(response.text, series_id, url)
'''

NEW_FRED_FUNCTION = r'''def fred_series_csv(series_id: str) -> dict[str, Any]:
    """Download one FRED series with runner-friendly timeout and controlled retry.

    GitHub-hosted runners can occasionally wait a long time for FRED's first
    response byte. Two explicit attempts are used instead of nested urllib3
    retries so the maximum runtime remains predictable.
    """
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={quote(series_id)}"
    last_error: Exception | None = None

    for attempt in range(1, 3):
        try:
            response = request(
                url,
                official=True,
                timeout=(5, 30),
                retries=0,
            )
            return parse_fred_series_csv(response.text, series_id, url)
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                delay = 2.0 * attempt
                log(
                    f"[FRED] {series_id} attempt={attempt}/2 failed; "
                    f"retrying in {delay:.0f}s: {type(exc).__name__}: {exc}"
                )
                time.sleep(delay)

    assert last_error is not None
    raise last_error
'''


def find_repo_root(start: Path) -> Path:
    candidates = [start, *start.parents]
    for candidate in candidates:
        if (candidate / "collector.py").is_file():
            return candidate

    # 사용자가 패치 폴더를 저장소 안에 넣지 않은 경우, 주변 폴더도 탐색한다.
    search_roots = [Path.cwd(), start.parent, Path.home() / "Documents" / "GitHub"]
    found: list[Path] = []
    for root in search_roots:
        if not root.exists():
            continue
        try:
            for path in root.rglob("collector.py"):
                if ".git" not in path.parts and "__pycache__" not in path.parts:
                    found.append(path.parent)
        except PermissionError:
            continue

    unique = []
    seen = set()
    for item in found:
        resolved = item.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)

    if len(unique) == 1:
        return unique[0]

    if not unique:
        raise FileNotFoundError(
            "collector.py를 찾지 못했습니다. 이 패치 폴더를 "
            "fed-futures-collector 폴더 안에 넣고 다시 실행하세요."
        )

    choices = "\n".join(f"  - {item}" for item in unique[:10])
    raise RuntimeError(
        "collector.py가 여러 곳에서 발견되어 자동 선택하지 않았습니다.\n"
        "패치 폴더를 수정할 저장소 폴더 안에 넣고 다시 실행하세요.\n"
        f"발견 위치:\n{choices}"
    )


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count == 1:
        return text.replace(old, new, 1)
    if count == 0:
        raise RuntimeError(f"{label} 원본 구문을 찾지 못했습니다. 이미 수정됐거나 파일 버전이 다릅니다.")
    raise RuntimeError(f"{label} 원본 구문이 {count}개 발견되어 안전상 중단했습니다.")


def patch_collector(repo: Path) -> Path:
    path = repo / "collector.py"
    original = path.read_text(encoding="utf-8")

    if "3.2.0-fred-timeout-controlled-retry" in original:
        print("[확인] collector.py는 이미 V3.2입니다.")
        return path

    updated = replace_once(
        original,
        OLD_FRED_FUNCTION,
        NEW_FRED_FUNCTION,
        "fred_series_csv 함수",
    )

    updated = replace_once(
        updated,
        "with ThreadPoolExecutor(max_workers=6) as executor:",
        "with ThreadPoolExecutor(max_workers=2) as executor:",
        "FRED 작업자 수",
    )

    updated = updated.replace(
        "# Six workers keeps total runtime low without hitting FRED too aggressively.",
        "# Two workers reduce simultaneous pressure on FRED and improve runner reliability.",
        1,
    )

    version_replacements = {
        "3.1.0-fred-date-parser-parallel": "3.2.0-fred-timeout-controlled-retry",
    }
    for old, new in version_replacements.items():
        updated = updated.replace(old, new)

    # 쓰기 전에 Python 문법을 검증한다.
    ast.parse(updated, filename=str(path))

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"collector.py.v3.1-backup-{stamp}")
    shutil.copy2(path, backup)
    path.write_text(updated, encoding="utf-8")

    print(f"[완료] 수정: {path}")
    print(f"[백업] 원본: {backup}")
    return path


def patch_workflows(repo: Path) -> None:
    workflow_dir = repo / ".github" / "workflows"
    if not workflow_dir.exists():
        return

    changed = 0
    for path in [*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")]:
        text = path.read_text(encoding="utf-8")
        updated = text.replace("RUNNING FED ENGINE V3.1", "RUNNING FED ENGINE V3.2")
        updated = updated.replace(
            "ENGINE_VERSION=3.1.0-fred-date-parser-parallel",
            "ENGINE_VERSION=3.2.0-fred-timeout-controlled-retry",
        )
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            changed += 1
            print(f"[완료] 워크플로 문구 수정: {path}")

    if changed == 0:
        print("[안내] 워크플로의 V3.1 문구는 없거나 별도 형식입니다. 실행에는 영향 없습니다.")


def main() -> int:
    try:
        script_dir = Path(__file__).resolve().parent
        repo = find_repo_root(script_dir)
        print(f"[저장소] {repo}")
        patch_collector(repo)
        patch_workflows(repo)

        print()
        print("V3.2 수정이 끝났습니다.")
        print("변경 내용:")
        print("  1) FRED 읽기 제한시간 10초 -> 30초")
        print("  2) FRED 동시 요청 6개 -> 2개")
        print("  3) 실패 시 명시적 1회 재시도(총 2회)")
        print("  4) 엔진 버전 V3.2로 변경")
        print()
        print("이제 GitHub Desktop으로 돌아가 변경 파일을 커밋한 뒤 Push origin을 누르세요.")
        return 0
    except Exception as exc:
        print()
        print("[실패]", exc)
        print()
        print("아무 파일도 임의로 덮어쓰지 않았거나, 쓰기 전 백업을 만들었습니다.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
