"""Stage 1: validate the candidate levers on the SEEN conversations only.

    python runs/stage1_seen_validation.py

The held-out seven stay untouched; they carry the final number. This stage
measures, on conv-26, conv-30 and conv-49:

1. **Extraction.** One run, two arms, so both stores come from the same engine,
   models and dedup policy and differ only in the extraction prompt: `llm`
   (shipped, the control) against `llm_wide` (the candidate). Retrieval is held at
   the shipped width for the final configuration, 25, on both arms, so the
   comparison is at the settings a promotion would ship.
2. **Store coverage, with no model in the loop.** `bench.extraction_coverage`
   asks the one question retrieval cannot: is a question's cited evidence present
   in any stored memory at all? That is the ceiling on what any read path could
   surface, and it reports store size beside it, because coverage bought by
   storing everything is not a win.
3. **Accuracy, paired per question.** The run answers and judges both arms under
   the same reader and the same strict judge, so the extraction effect is
   isolated from retrieval and from grading.

Each step runs under the supervisor, so a usage cap pauses and the same command
resumes. An expired session halts instead, because waiting cannot fix it.

It deliberately stops here. Folding a lever into the combined engine is a
judgement against the pre-registered rule (mechanism, plus an effect larger than
the spread between runs, at no adversarial cost), not something this script
decides.
"""
import datetime
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
PY = sys.executable
LOG = REPO / "runs" / "stage1.log"
RUN = "extract-ab-seen"
SEEN = "conv-26,conv-30,conv-49"
SPACE = f"bench-{RUN}-conversational"
# The settle database, not the levers one: this run's stores stay beside the
# held-out run's, and the two arms are kept apart by scope, not by database.
DSN = "postgres://postgres:engraphy@127.0.0.1:5442/engraphy_bench?sslmode=disable"
GAP_LABELS = ("../engraphy-benchmarks-completeness/analysis/"
              "2026-09-30-extraction-gap-labels.json")


def log(msg: str) -> None:
    line = f"[{datetime.datetime.now().astimezone().isoformat(timespec='seconds')}] {msg}"
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def supervised(cmd: list[str], run_dir: str, log_name: str) -> int:
    full = [PY, "-m", "bench.supervise", "--run-dir", run_dir, "--log", log_name, "--", *cmd]
    out = (REPO / "runs" / f"{pathlib.Path(log_name).stem}.stdout").open("a", encoding="utf-8")
    rc = subprocess.run(full, cwd=REPO, stdout=out, stderr=subprocess.STDOUT,
                        check=False).returncode
    log(f"exit {rc}")
    return rc


os.environ.setdefault("ENGRAPHY_TEST_DATABASE_URL", DSN)

bad = [v for v in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN") if os.environ.get(v)]
if bad:
    log(f"refusing to start: {bad} set; the CLI route must use the subscription")
    sys.exit(2)

head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                      text=True, check=False).stdout.strip()
log(f"stage 1 start, engine {head}, seen split {SEEN}")

both_arms = [PY, "-m", "bench.core.run", "--dataset", "datasets/locomo10.json",
             "--haystacks", SEEN,
             "--arm", "llm-conversational:search_only:k=25",
             "--arm", "llm_wide-conversational:search_only:k=25",
             "--judge", "claude", "--reader-stance", "grounded", "--reader-contract", "verify",
             "--phases", "ingest,answer,judge,report",
             "--concurrency", "3", "--judge-concurrency", "4",
             "--run-id", RUN]
rc = supervised(both_arms, f"runs/{RUN}", f"runs/{RUN}.supervise.log")
if rc != 0:
    log("the two-arm run did not finish; rerun this script to resume")
    sys.exit(1)
log("TWO-ARM RUN COMPLETE")

coverage = [PY, "-m", "bench.extraction_coverage",
            "--space", SPACE, "--extractor", "llm",
            "--space", SPACE, "--extractor", "llm_wide",
            "--haystacks", SEEN,
            *(("--gap-labels", GAP_LABELS) if (REPO / GAP_LABELS).exists() else ()),
            "--out", f"runs/{RUN}/coverage.json"]
log("store coverage, no model in the loop")
rc = subprocess.run(coverage, cwd=REPO, check=False).returncode
log(f"coverage exit {rc}")
log("STAGE 1 COMPLETE")
