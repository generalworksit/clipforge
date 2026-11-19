"""Check staged files before publishing. Reports paths, never secret values."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
names = subprocess.check_output(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"], cwd=ROOT).decode().split("\0")
blocked = {"jobs", "models", "monitor-data", ".venv", "artifacts", "_backup_2026-11-19"}
patterns = [rb"AIza[0-9A-Za-z_-]{35}", rb"gh[pousr]_[0-9A-Za-z]{30,}", rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"]
local_secrets = []
if (ROOT / ".live-token").is_file():
    local_secrets.append((ROOT / ".live-token").read_bytes().strip())
problems = []
total = 0
for name in filter(None, names):
    path = Path(name)
    if set(path.parts) & blocked or path.name in {".live-token", "config.yaml", ".env"} or path.suffix in {".log", ".dpapi", ".sqlite3"}:
        problems.append((name, "local/runtime file")); continue
    data = subprocess.check_output(["git", "show", ":" + name], cwd=ROOT)
    total += len(data)
    if len(data) > 50 * 1024**2:
        problems.append((name, "oversized file"))
    if any(re.search(p, data) for p in patterns) or any(s and s in data for s in local_secrets):
        problems.append((name, "possible credential"))
if problems:
    for name, reason in problems: print(f"BLOCKED: {name}: {reason}")
    raise SystemExit(1)
print(f"PASS: {sum(bool(n) for n in names)} staged files, {total / 1024**2:.2f} MiB; no matched runtime paths or credential patterns.")
