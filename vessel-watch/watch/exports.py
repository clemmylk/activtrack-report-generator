"""Offline Excel export; the local spreadsheet runtime uses no AI or API calls."""
import json
import os
import subprocess
from pathlib import Path


def export_excel(json_path, output_path, preview=False):
    root = Path(__file__).resolve().parents[1]
    runtime_file = root / "runtime.json"
    runtime = json.loads(runtime_file.read_text()) if runtime_file.exists() else {}
    default = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
    node = Path(runtime.get("node", str(default)))
    if not node.is_file():
        raise RuntimeError("Excel export runtime not configured on this PC. Your JSON and HTML reports are saved.")
    args = [str(node), str(root / "export_excel.mjs"), str(Path(json_path).resolve()), str(Path(output_path).resolve())]
    if preview:
        args.append("--preview")
    result = subprocess.run(args, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError("Excel export failed: " + (result.stderr or result.stdout)[-1200:])
    if not Path(output_path).is_file() or Path(output_path).stat().st_size < 100:
        raise RuntimeError("Excel export did not produce a valid file.")
    return Path(output_path)
