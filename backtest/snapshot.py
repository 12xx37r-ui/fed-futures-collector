from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    latest_path = Path("public/data/latest.json")
    history_path = Path("public/data/history.json")
    if not latest_path.exists():
        raise FileNotFoundError("latest.json missing")

    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []

    key = f"{latest.get('generated_at_utc')}|{latest.get('next_fomc')}"
    if not any(x.get("snapshot_key") == key for x in history):
        history.append({
            "snapshot_key": key,
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "meeting": latest.get("next_fomc"),
            "probabilities": latest.get("probabilities"),
            "features": latest.get("features"),
            "confidence": latest.get("confidence"),
            "actual_direction": None,
        })
    history_path.write_text(json.dumps(history[-5000:], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"history rows: {len(history)}")


if __name__ == "__main__":
    main()
