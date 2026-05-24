"""
run_forecast.py  --  Watchdog wrapper for inventory_forecaster.py

Two-layer retry stack:

  Inner (watchdog): launches the forecaster as a subprocess and monitors stdout.
                    If no new output for STALL_TIMEOUT_SECS, kills + restarts
                    up to MAX_INNER_RESTARTS times.

  Outer (cool-off): if the inner watchdog gives up (process truly hung or
                    crashed N times in a row), waits a cool-off period and
                    retries the whole watchdog session up to MAX_OUTER_RETRIES.
                    Cool-off schedule (matches SKILL.md retry protocol):
                       Try 2-3:   3 min wait
                       Try 4-10:  +5 min each (3, 8, 13, 18, 23, 28, 33 min)

The outer layer replaces what was previously a manual workflow in SKILL.md.
Now the user does NOT need to wrap this script in a bash retry loop.

Usage (same flags as inventory_forecaster.py + watchdog tuning):
    python run_forecast.py --all --validate
    python run_forecast.py --acct 1864 --validate
    python run_forecast.py --all --max-outer-retries 5    # cap at 5 outer retries
    python run_forecast.py --all --no-outer-retry         # disable outer layer

A run is considered SUCCESSFUL when:
    - Process exits with code 0, AND
    - stdout contained either "COMPLETE" or "[analyze-only] Done"

If the inner watchdog exhausts MAX_INNER_RESTARTS, that's treated as one
"outer failure" and triggers the cool-off + retry.
"""

import subprocess
import sys
import time
import os
import threading
import argparse


STALL_TIMEOUT_SECS  = 1200  # 20 min without output = truly hung
HEARTBEAT_SECS      = 60    # print "[watchdog] still running..." every minute
MAX_INNER_RESTARTS  = 3     # kill+restart up to this many times within one watchdog session
SCRIPT              = os.path.join(os.path.dirname(__file__), "inventory_forecaster.py")

# Outer cool-off schedule (minutes). Index = retry attempt - 1.
# Try 1 has no wait (initial run). Try 2-3 = 3 min. Try 4+ adds 5 min each.
OUTER_RETRY_COOL_OFFS_MIN = [0, 3, 3, 8, 13, 18, 23, 28, 33, 38]
DEFAULT_MAX_OUTER_RETRIES = len(OUTER_RETRY_COOL_OFFS_MIN)   # = 10

# Output markers that indicate a successful run.
SUCCESS_MARKERS = ("COMPLETE", "[analyze-only] Done")


def run_with_watchdog(args, attempt=1, output_marker_seen=None):
    """One watchdog session. Returns (exit_code, marker_seen_bool)."""
    if output_marker_seen is None:
        output_marker_seen = [False]

    print(f"\n{'='*60}", flush=True)
    print(f"  [watchdog] Starting forecaster (inner attempt {attempt}/{MAX_INNER_RESTARTS})", flush=True)
    print(f"{'='*60}", flush=True)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        [sys.executable, SCRIPT] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        env=env,
    )

    last_output_time = [time.time()]
    last_line        = ["(none)"]
    done             = [False]

    def reader():
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            last_output_time[0] = time.time()
            last_line[0]        = line.rstrip()
            # Detect success markers as the run progresses.
            for marker in SUCCESS_MARKERS:
                if marker in line:
                    output_marker_seen[0] = True
        done[0] = True

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    last_hb = time.time()

    while not done[0]:
        time.sleep(10)
        if done[0]:
            break
        now   = time.time()
        stall = now - last_output_time[0]

        if now - last_hb >= HEARTBEAT_SECS:
            last_hb = now
            print(f"  [watchdog] still running -- {stall:.0f}s since last output "
                  f"| last: {last_line[0][:80]}", flush=True)

        if stall >= STALL_TIMEOUT_SECS:
            print(f"\n  [watchdog] !! NO OUTPUT FOR {stall:.0f}s -- process appears hung.", flush=True)
            print(f"  [watchdog] Last line: {last_line[0]}", flush=True)
            print(f"  [watchdog] Killing PID {proc.pid} and restarting ...\n", flush=True)
            proc.kill()
            proc.wait()
            if attempt < MAX_INNER_RESTARTS:
                return run_with_watchdog(args, attempt + 1, output_marker_seen)
            else:
                print(f"  [watchdog] Reached max inner restarts ({MAX_INNER_RESTARTS}).", flush=True)
                return 1, output_marker_seen[0]

    t.join()
    proc.wait()

    if proc.returncode != 0:
        print(f"\n  [watchdog] Process exited with code {proc.returncode}.", flush=True)
        if attempt < MAX_INNER_RESTARTS:
            print(f"  [watchdog] Restarting (inner attempt {attempt+1}/{MAX_INNER_RESTARTS}) ...", flush=True)
            time.sleep(5)
            return run_with_watchdog(args, attempt + 1, output_marker_seen)
        return proc.returncode, output_marker_seen[0]

    print(f"\n  [watchdog] Forecaster completed (exit 0).", flush=True)
    return 0, output_marker_seen[0]


def run_with_outer_retry(args, max_outer_retries):
    """Outer cool-off loop. Wraps the watchdog with a cool-off + restart layer."""
    for outer in range(1, max_outer_retries + 1):
        if outer > 1:
            cool_off_min = OUTER_RETRY_COOL_OFFS_MIN[
                min(outer - 1, len(OUTER_RETRY_COOL_OFFS_MIN) - 1)
            ]
            print(f"\n{'#'*60}", flush=True)
            print(f"  [outer-retry] Attempt {outer}/{max_outer_retries} -- "
                  f"cooling off {cool_off_min} min before retry", flush=True)
            print(f"{'#'*60}", flush=True)
            time.sleep(cool_off_min * 60)

        exit_code, marker_seen = run_with_watchdog(args, attempt=1)

        if exit_code == 0 and marker_seen:
            print(f"\n  [outer-retry] SUCCESS on outer attempt {outer}.", flush=True)
            return 0
        if exit_code == 0 and not marker_seen:
            # Process exited cleanly but didn't print a success marker.
            # Treat as suspicious; retry to be safe.
            print(f"\n  [outer-retry] Exit 0 but no success marker seen "
                  f"(COMPLETE / [analyze-only] Done). Treating as failure.", flush=True)
        else:
            print(f"\n  [outer-retry] Inner watchdog failed (exit {exit_code}).", flush=True)

    print(f"\n  [outer-retry] EXHAUSTED {max_outer_retries} outer retries. Giving up.", flush=True)
    return 1


def main():
    # Parse our own watchdog flags first, leave the rest for the forecaster.
    p = argparse.ArgumentParser(description="Watchdog wrapper for inventory_forecaster.py",
                                add_help=False)
    p.add_argument("--max-outer-retries", type=int, default=DEFAULT_MAX_OUTER_RETRIES,
                   help=f"Outer retry attempts after inner watchdog gives up "
                        f"(default: {DEFAULT_MAX_OUTER_RETRIES}). "
                        f"Cool-off schedule per attempt: {OUTER_RETRY_COOL_OFFS_MIN[1:]} min.")
    p.add_argument("--no-outer-retry", action="store_true",
                   help="Disable outer retry layer (inner watchdog only).")
    p.add_argument("--watchdog-help", action="store_true",
                   help="Show this watchdog help and exit (forecaster gets --help separately).")
    args, forecaster_args = p.parse_known_args()

    if args.watchdog_help:
        p.print_help()
        sys.exit(0)

    max_retries = 1 if args.no_outer_retry else args.max_outer_retries
    sys.exit(run_with_outer_retry(forecaster_args, max_retries))


if __name__ == "__main__":
    main()
