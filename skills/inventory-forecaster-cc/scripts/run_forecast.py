"""
run_forecast.py  —  Watchdog wrapper for inventory_forecaster.py

Launches the forecaster as a subprocess and monitors its output for stalls.
If no new output is seen for STALL_TIMEOUT_SECS, the process is killed and
automatically restarted (up to MAX_RESTARTS times).

Usage (same flags as inventory_forecaster.py):
    python run_forecast.py --all --validate --push-validation
    python run_forecast.py --acct 1864 --validate
"""

import subprocess
import sys
import time
import os
import threading

STALL_TIMEOUT_SECS  = 1200  # 20 min without output = truly hung
HEARTBEAT_SECS      = 60    # print "[watchdog] still running..." every minute
MAX_RESTARTS        = 3
SCRIPT              = os.path.join(os.path.dirname(__file__), "inventory_forecaster.py")


def run_with_watchdog(args, attempt=1):
    print(f"\n{'='*60}", flush=True)
    print(f"  [watchdog] Starting forecaster (attempt {attempt}/{MAX_RESTARTS})", flush=True)
    print(f"{'='*60}", flush=True)

    # PYTHONUNBUFFERED=1 forces the child process to flush every print() immediately,
    # even when stdout is a pipe.  Without it, buffered output can delay phase
    # headers by minutes and make the watchdog think the process is hung.
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        [sys.executable, SCRIPT] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
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
        done[0] = True

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    last_hb = time.time()

    # Watchdog loop — checks every 10s, prints a heartbeat every 60s
    while not done[0]:
        time.sleep(10)
        if done[0]:
            break
        now   = time.time()
        stall = now - last_output_time[0]

        # Periodic heartbeat so we (and the user) know the job is alive
        if now - last_hb >= HEARTBEAT_SECS:
            last_hb = now
            print(f"  [watchdog] still running — {stall:.0f}s since last output "
                  f"| last: {last_line[0][:80]}", flush=True)

        if stall >= STALL_TIMEOUT_SECS:
            print(f"\n  [watchdog] !! NO OUTPUT FOR {stall:.0f}s — process appears hung.", flush=True)
            print(f"  [watchdog] Last line: {last_line[0]}", flush=True)
            print(f"  [watchdog] Killing PID {proc.pid} and restarting ...\n", flush=True)
            proc.kill()
            proc.wait()
            if attempt < MAX_RESTARTS:
                return run_with_watchdog(args, attempt + 1)
            else:
                print(f"  [watchdog] Reached max restarts ({MAX_RESTARTS}). Giving up.", flush=True)
                return 1

    t.join()
    proc.wait()

    if proc.returncode != 0:
        print(f"\n  [watchdog] Process exited with code {proc.returncode}.", flush=True)
        if attempt < MAX_RESTARTS:
            print(f"  [watchdog] Restarting (attempt {attempt+1}/{MAX_RESTARTS}) ...", flush=True)
            time.sleep(5)
            return run_with_watchdog(args, attempt + 1)
        return proc.returncode

    print(f"\n  [watchdog] Forecaster completed successfully.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(run_with_watchdog(sys.argv[1:]))
