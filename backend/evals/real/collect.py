"""下载 sources.py 里的文档到 docs/，写 manifest.csv。用法：python -m evals.real.collect"""
import csv
import subprocess
import time
from pathlib import Path

from evals.real.sources import SOURCES

HERE = Path(__file__).parent
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"

def sniff(p: Path) -> str:
    head = p.read_bytes()[:8]
    if head.startswith(b"%PDF"): return "pdf"
    if head.startswith(b"PK"): return "docx"
    if head.startswith(b"\xd0\xcf\x11\xe0"): return "doc"
    return ""

def main():
    rows = []
    for sid, url, title, issuer, date, lang, kind in SOURCES:
        have = [p for p in (HERE / "docs").glob(sid + ".*")]
        if not have:
            tmp = HERE / "docs" / (sid + ".part")
            ua = "GRC-Helper-Lite eval research pz1130@users.noreply.github.com" if "sec.gov" in url else UA
            subprocess.run(["curl", "-sL", "--retry", "3", "-m", "180", "-A", ua, "-o", str(tmp), url], check=False)
            fmt = sniff(tmp) if tmp.exists() else ""
            if not fmt:
                print("FAIL", sid, url); tmp.unlink(missing_ok=True); continue
            tmp.rename(HERE / "docs" / f"{sid}.{fmt}")
            time.sleep(3)
            have = [HERE / "docs" / f"{sid}.{fmt}"]
        f = have[0]
        rows.append({"id": sid, "file": f"docs/{f.name}", "format": f.suffix[1:], "lang": lang, "kind": kind,
                         "title": title, "issuer": issuer, "date": date, "url": url, "bytes": f.stat().st_size})
    with open(HERE / "manifest.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print(len(rows), "docs")

if __name__ == "__main__":
    main()
