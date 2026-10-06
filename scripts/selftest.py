#!/usr/bin/env python3
"""End-to-end self-test on the bundled fixture app (macOS, Windows, Linux).

USAGE
  selftest.py [WORKDIR] [--no-tts] [--quick]
Runs unit tests and lint, then for both modes: rehearse, record, (narration), edit final, verify,
export, redact, report. --quick skips the bug-report pass. Default WORKDIR: a new temp folder.
Narration runs only when the Piper voice is installed (install_tts.py) or on macOS (`say`).
Exit code 1 on the first failing stage.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
PY = sys.executable


def run(args, cwd=None, capture=False):
    r = subprocess.run([str(a) for a in args], cwd=cwd, text=True, encoding="utf-8", errors="replace",
                       capture_output=capture)
    if r.returncode != 0:
        if capture:
            print(r.stdout[-3000:], r.stderr[-3000:], sep="\n", file=sys.stderr)
        sys.exit(f"selftest FAILED at: {' '.join(str(a) for a in args[:3])} …")
    return r


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workdir", nargs="?", type=pathlib.Path)
    ap.add_argument("--no-tts", action="store_true")
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    work = (a.workdir or pathlib.Path(tempfile.mkdtemp(prefix="web-video-selftest-"))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    os.environ["PYTHONIOENCODING"] = "utf-8"

    print("== unit tests", flush=True)
    run([PY, "-m", "unittest", "discover", "-s", ROOT / "tests", "-q"])
    print("== lint", flush=True)
    run([PY, HERE / "lint_skill.py", ROOT])

    sys.path.insert(0, str(HERE))
    from install_tts import installed as piper_ok
    tts = not a.no_tts and (piper_ok() or (platform.system() == "Darwin" and shutil.which("say")))

    port = free_port()
    log = open(work / "fixture-server.log", "w", encoding="utf-8")
    server = subprocess.Popen([PY, ROOT / "assets" / "fixture" / "server.py", str(port)], stdout=log, stderr=log)
    try:
        deadline = time.time() + 60                      # cold CI runners can take a while to start Python
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), 0.5).close()
                break
            except OSError:
                if server.poll() is not None or time.time() > deadline:
                    log.flush()
                    sys.exit("fixture server did not start:\n" + (work / "fixture-server.log").read_text(encoding="utf-8"))
                time.sleep(0.2)
        for mode in ("feature-demo",) if a.quick else ("feature-demo", "bug-report"):
            print(f"== {mode}", flush=True)
            sb = work / f"{mode}.yaml"
            text = (ROOT / "assets" / "examples" / f"{mode}.yaml").read_text(encoding="utf-8")
            sb.write_text(text.replace("http://localhost:8765", f"http://localhost:{port}"), encoding="utf-8")
            run([PY, HERE / "record.py", sb, "--rehearse"], cwd=work, capture=True)
            out = run([PY, HERE / "record.py", sb, "--slug", mode, "--root", work / "video-out"],
                      cwd=work, capture=True).stdout
            rd = next((l.split("run dir: ", 1)[1].strip() for l in out.splitlines() if l.startswith("run dir: ")), "")
            rdir = pathlib.Path(rd)
            if not rdir.is_dir():
                sys.exit("record did not report a run dir")
            if mode == "feature-demo" and tts:
                run([PY, HERE / "tts.py", rdir], capture=True)
            run([PY, HERE / "edl.py", rdir, "--render", "final"])
            v = subprocess.run([PY, HERE / "verify.py", rdir, "--video", "edited.mp4"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            print("\n".join(l for l in v.stdout.splitlines() if l.startswith(("FAIL", "WARN", "contact"))))
            if v.returncode != 0:
                sys.exit(f"verify failed: see {rdir / 'verify' / 'report.md'}")
            run([PY, HERE / "export.py", rdir / "edited.mp4", rdir / "final.mp4"])
            if mode == "bug-report":
                run([PY, HERE / "redact.py", rdir], capture=True)
            run([PY, HERE / "report.py", rdir], capture=True)
    finally:
        server.terminate()
        log.close()
    print(f"selftest OK ({'with' if tts else 'without'} narration). Videos: {work / 'video-out'}")


if __name__ == "__main__":
    main()
