"""
Fail if anything that looks like personal account data is about to be committed.

This exists because it already happened once on the TECO project: real account
and invoice numbers were pasted into a public pull request body, and neither a
force-push nor editing the description removed them (GitHub retains both the
orphaned commits and the description edit history).

Run manually, from CI, and from .git/hooks/pre-commit:
    python scripts/check_no_pii.py            # scan tracked files
    python scripts/check_no_pii.py --staged   # scan what is staged (pre-commit)
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

# Patterns for the kinds of identifier this project handles.
PATTERNS = [
    # Tampa CSS accounts are 7 digits, TECO contract accounts 12, invoices ~12.
    # Match any long digit run rather than exact lengths -- an off-by-one length
    # is exactly how a real account number slips through.
    ("account-number-like", re.compile(r"(?<!\d)\d{7,18}(?!\d)")),
    ("street-address", re.compile(r"\b\d{3,6}\s+[NSEW]\.?\s+\w+\s+(ST|AVE|BLVD|DR|RD|LN|CT|PL|WAY)\b", re.I)),
    ("meter-serial", re.compile(r"\b[A-Z]{2,4}\d{5,}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")),
]

# Files that legitimately contain digit runs (version pins, hashes, CI SHAs).
SKIP_FILES = {".gitignore", "requirements.txt", "poetry.lock"}
SKIP_DIRS = (".git/", ".venv/", "recon_out/", "debug_out/", "fixtures/", "__pycache__/")
# CI action pins and the like: 40-hex SHAs, ISO dates, and obvious placeholders.
ALLOW = re.compile(r"[0-9a-f]{40}|<[a-z][a-z-]*>|N{4,}|X{6,}|x{6,}"
                   r"|example\.com|noreply@|0{7,}")


def tracked_files(staged: bool) -> list[str]:
    cmd = (["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"]
           if staged else ["git", "ls-files"])
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    return [f for f in out.splitlines()
            if f and not f.startswith(SKIP_DIRS) and f.rsplit("/", 1)[-1] not in SKIP_FILES]


def scan(path: str) -> list[tuple[int, str, str]]:
    hits = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for n, line in enumerate(f, 1):
                if ALLOW.search(line):
                    continue
                for name, pat in PATTERNS:
                    m = pat.search(line)
                    if m:
                        hits.append((n, name, m.group(0)))
    except (OSError, IsADirectoryError):
        pass
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", action="store_true")
    args = ap.parse_args()

    findings = []
    for path in tracked_files(args.staged):
        for n, name, value in scan(path):
            # never echo PII into CI logs: type + length + last 2 only
            shown = f"<{len(value)} chars ending {value[-2:]}>"
            findings.append(f"  {path}:{n}  [{name}]  {shown}")

    if findings:
        print("Possible personal data found. Nothing was committed.\n")
        print("\n".join(findings))
        print("\nUse a placeholder (<account-id>, ...NNNN) or add the file to .gitignore.")
        print("If this is a false positive, add it to ALLOW in scripts/check_no_pii.py.")
        return 1
    print(f"no PII patterns found in {'staged' if args.staged else 'tracked'} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
