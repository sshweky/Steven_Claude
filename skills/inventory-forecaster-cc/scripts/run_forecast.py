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
from collections import deque
from datetime import datetime


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

# Failure email settings.
FAILURE_BUFFER_LINES      = 150   # how many tail lines to include in the failure email
FAILURE_EMAIL_RECIPIENT   = "s.shweky@petspeople.com"


def _send_failure_email(output_tail, forecaster_args, outer_attempts_used):
    """Send failure notification email via the shared send_email utility."""
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        from send_email import send_email as _smtp_send  # noqa: PLC0415
    except ImportError as _ie:
        print(f"  [failure-email] Could not import send_email: {_ie}", flush=True)
        return

    ts         = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    date_short = datetime.now().strftime("%Y-%m-%d")
    subject    = f"Forecaster FAILED -- {date_short}"
    args_str   = " ".join(forecaster_args) if forecaster_args else "(none)"

    # Build HTML rows for the tail output (escape HTML entities).
    tail_rows = "\n".join(
        "<tr><td style='font-family:monospace;font-size:12px;white-space:pre;"
        "padding:1px 8px;color:#d4d4d4;'>"
        + line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        + "</td></tr>"
        for line in output_tail
    )

    body_html = f"""<html><body style="font-family:Arial,sans-serif;font-size:13px;color:#222;">
<h2 style="color:#c0392b;">Inventory Forecaster -- Run FAILED</h2>
<table cellpadding="4" style="margin-bottom:14px;border-collapse:collapse;">
  <tr><td><b>Timestamp:</b></td><td>{ts}</td></tr>
  <tr><td><b>Args:</b></td><td><code>{args_str}</code></td></tr>
  <tr><td><b>Outer attempts exhausted:</b></td><td>{outer_attempts_used}</td></tr>
</table>
<p>All {outer_attempts_used} outer retry attempt(s) were exhausted without a successful
completion. The last {len(output_tail)} lines of output are shown below -- look for
NameError, Traceback, or [WARN] lines to identify the root cause.</p>
<h3 style="margin-bottom:4px;">Last output (tail):</h3>
<table style="background:#1e1e1e;border-radius:4px;width:100%;border-collapse:collapse;
              padding:6px;">
{tail_rows}
</table>
</body></html>"""

    print(f"  [failure-email] Sending failure notification to {FAILURE_EMAIL_RECIPIENT} ...",
          flush=True)
    try:
        ok = _smtp_send(subject, body_html, to=FAILURE_EMAIL_RECIPIENT, is_html=True)
        if ok:
            print(f"  [failure-email] Sent successfully.", flush=True)
        else:
            print(f"  [failure-email] send_email returned False (check SMTP config).", flush=True)
    except Exception as _ex:
        print(f"  [failure-email] Exception while sending: {_ex}", flush=True)


def run_with_watchdog(args, attempt=1, output_marker_seen=None, output_buffer=None):
    """One watchdog session. Returns (exit_code, marker_seen_bool).

    output_buffer is a deque shared across all inner restart attempts so that
    run_with_outer_retry() can read the full tail for the failure email.
    """
    if output_marker_seen is None:
        output_marker_seen = [False]
    if output_buffer is None:
        output_buffer = deque(maxlen=FAILURE_BUFFER_LINES)

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
            stripped = line.rstrip()
            last_line[0] = stripped
            output_buffer.append(stripped)   # keep rolling tail for failure email
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
                return run_with_watchdog(args, attempt + 1, output_marker_seen, output_buffer)
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
            return run_with_watchdog(args, attempt + 1, output_marker_seen, output_buffer)
        return proc.returncode, output_marker_seen[0]

    print(f"\n  [watchdog] Forecaster completed (exit 0).", flush=True)
    return 0, output_marker_seen[0]


def run_with_outer_retry(args, max_outer_retries):
    """Outer cool-off loop. Wraps the watchdog with a cool-off + restart layer.

    A single output_buffer deque is shared across all outer attempts so the
    failure email contains a rolling tail of the most recent output regardless
    of which attempt produced it.
    """
    output_buffer = deque(maxlen=FAILURE_BUFFER_LINES)

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

        exit_code, marker_seen = run_with_watchdog(args, attempt=1,
                                                   output_buffer=output_buffer)

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
    _send_failure_email(list(output_buffer), args, max_outer_retries)
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
