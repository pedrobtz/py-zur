"""Measure what the launcher adds to R's start (design §5).

    uv run scripts/start_cost.py [--runs 21]

Times ``Rscript -e 1`` started four ways with the installed tool and
prints each median and its difference from R started without the
launcher (``$R_HOME/bin/Rscript``):

- ``bare``: no launcher;
- ``stamped``: the launcher, the first-run stamp already written (every
  start after the first);
- ``no check``: the launcher with ``ZUR_NO_CHECK=1``;
- ``first run``: the launcher in a fresh home each time, so it runs the
  check and writes the stamp.

A Markdown table goes to stdout, for the job summary.
"""

import argparse
import os
import shutil
import statistics
import subprocess
import tempfile
import time


def timed(argv, env):
    start = time.perf_counter()
    subprocess.run(argv, env=env, check=True, capture_output=True)
    return time.perf_counter() - start


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=21)
    args = parser.parse_args()

    rscript = shutil.which("Rscript")
    if rscript is None:
        raise SystemExit("start_cost.py: Rscript is not on PATH")
    base = {k: v for k, v in os.environ.items() if k != "ZUR_NO_CHECK"}
    r_home = subprocess.run(
        [rscript, "-e", "cat(R.home())"],
        env=dict(base, ZUR_NO_CHECK="1"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    bare = os.path.join(r_home, "bin", "Rscript")

    with tempfile.TemporaryDirectory() as tmp:
        stamped = dict(base, HOME=os.path.join(tmp, "stamped"))
        os.mkdir(stamped["HOME"])
        timed([rscript, "-e", "1"], stamped)  # writes the stamp

        def fresh():
            home = tempfile.mkdtemp(dir=tmp)
            return [rscript, "-e", "1"], dict(base, HOME=home)

        cases = {
            "bare": lambda: ([bare, "-e", "1"], stamped),
            "stamped": lambda: ([rscript, "-e", "1"], stamped),
            "no check": lambda: ([rscript, "-e", "1"], dict(stamped, ZUR_NO_CHECK="1")),
            "first run": fresh,
        }
        times = {name: [] for name in cases}
        # Interleaved, so a slow patch of the machine hits every case.
        for _ in range(args.runs):
            for name, case in cases.items():
                times[name].append(timed(*case()))

    medians = {name: statistics.median(t) * 1000 for name, t in times.items()}
    print(f"Start of `Rscript -e 1`, median of {args.runs} runs:\n")
    print("| Case | Median (ms) | Launcher adds (ms) |")
    print("| --- | ---: | ---: |")
    for name, ms in medians.items():
        print(f"| {name} | {ms:.1f} | {ms - medians['bare']:.1f} |")


if __name__ == "__main__":
    main()
