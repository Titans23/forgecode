"""Cache a pinned Harbor dataset's prebuilt images without running evaluations."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time
import tomllib


def now():
    return datetime.now(timezone.utc).isoformat()


def inspect(image):
    result = subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True, timeout=45)
    if result.returncode:
        return None
    data = json.loads(result.stdout)[0]
    return {"id": data["Id"], "repo_digests": data.get("RepoDigests", []),
            "size_bytes": data["Size"], "os": data["Os"], "architecture": data["Architecture"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--pull-timeout-seconds", type=float, default=7200)
    parser.add_argument("--size-manifest", type=Path, help="Optional image-size metadata; download smaller images first")
    args = parser.parse_args()
    if args.concurrency < 1 or args.pull_timeout_seconds <= 0:
        parser.error("concurrency and pull timeout must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    records = []
    cache = Path.home() / ".cache/harbor/tasks/packages"
    for task in catalog["task_ids"]:
        config = cache / task["org"] / task["name"] / task["ref"].split(":", 1)[1] / "task.toml"
        image = tomllib.loads(config.read_text(encoding="utf-8"))["environment"]["docker_image"]
        records.append({"task": task["name"], "ref": task["ref"], "image": image, "state": "pending"})
    state = {"dataset": catalog["name"], "version": catalog["version"], "pid": os.getpid(),
             "started_at": now(), "finished_at": None, "concurrency": args.concurrency,
             "pull_timeout_seconds": args.pull_timeout_seconds,
             "total": len(records), "records": records}

    def persist():
        state["updated_at"] = now()
        state["ready"] = sum(r["state"] == "ready" for r in records)
        state["failed"] = sum(r["state"] == "failed" for r in records)
        state["pending"] = sum(r["state"] == "pending" for r in records)
        temporary = args.output / "status.tmp"
        temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
        temporary.replace(args.output / "status.json")

    for record in records:
        info = inspect(record["image"])
        if info:
            record.update(state="ready", initially_cached=True, inspected_at=now(), **info)
        else:
            record["initially_cached"] = False
    state["initially_cached"] = sum(r["initially_cached"] for r in records)
    persist()
    print(f"CACHE {state['ready']}/{state['total']}; pending {state['pending']}", flush=True)

    def pull(record):
        log_path = args.output / (record["task"] + ".log")
        started = now()
        with log_path.open("a", encoding="utf-8") as log:
            for attempt in range(1, 4):
                print(f"PULL {record['task']} attempt={attempt}", flush=True)
                log.write(f"\n{now()} attempt {attempt}\n")
                log.flush()
                try:
                    result = subprocess.run(["docker", "pull", record["image"]], stdout=log, stderr=subprocess.STDOUT, timeout=args.pull_timeout_seconds)
                    info = inspect(record["image"])
                    if info:
                        return {"state": "ready", "attempts": attempt, "pull_started_at": started,
                                "inspected_at": now(), "log_path": str(log_path.resolve()), **info}
                    error = f"docker pull exited {result.returncode}; image absent"
                except (subprocess.TimeoutExpired, OSError) as exc:
                    error = str(exc)
                    log.write(error + "\n")
                if attempt < 3:
                    time.sleep(5 * attempt)
        return {"state": "failed", "attempts": 3, "error": error,
                "pull_started_at": started, "finished_at": now(), "log_path": str(log_path.resolve())}

    sizes = {}
    if args.size_manifest:
        sizes = {item["image"]: item.get("compressed_size_bytes", 0)
                 for item in json.loads(args.size_manifest.read_text(encoding="utf-8"))
                 if "image" in item}
    pending = sorted((record for record in records if record["state"] == "pending"),
                     key=lambda record: sizes.get(record["image"], 0))
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(pull, record): record for record in pending}
        for future in as_completed(futures):
            record = futures[future]
            try:
                record.update(future.result())
            except Exception as exc:
                record.update(state="failed", error=str(exc))
            persist()
            print(f"{record['state'].upper()} {record['task']} {state['ready']}/{state['total']}", flush=True)
    state["finished_at"] = now()
    persist()
    print(f"DONE ready={state['ready']} failed={state['failed']} total={state['total']}", flush=True)
    return 1 if state["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
