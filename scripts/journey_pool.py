"""Balance every required Python case; only explicit CI runs have a whole-run deadline."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor


def case_key(nodeid):
    unparameterized, bracket, parameters = nodeid.partition("[")
    path, *parts = unparameterized.split("::")
    classname = path.removesuffix(".py").replace("/", ".")
    if len(parts) > 1:
        classname += "." + ".".join(parts[:-1])
    return classname + "::" + parts[-1] + bracket + parameters


def balanced_assignments(tasks, count, timings, *, affinity=False):
    if affinity:
        modules = {}
        for task in tasks:
            modules.setdefault(task[1].split("::")[0], []).append(task)
        buckets = [[] for _ in range(count)]
        loads = [0.0] * count
        for _module, cohort in sorted(
            modules.items(),
            key=lambda pair: (
                -sum(timings.get(case_key(task[1]), 5.0) for task in pair[1]),
                pair[0],
            ),
        ):
            index = min(range(count), key=lambda item: (loads[item], item))
            buckets[index].extend(cohort)
            loads[index] += sum(timings.get(case_key(task[1]), 5.0) for task in cohort)
        return buckets, loads
    buckets = [[] for _ in range(count)]
    loads = [0.0] * count
    ordinal = {task: index for index, task in enumerate(tasks)}
    for task in sorted(
        tasks, key=lambda item: (-timings.get(case_key(item[1]), 5.0), ordinal[item])
    ):
        index = min(range(count), key=lambda item: (loads[item], item))
        buckets[index].append(task)
        loads[index] += timings.get(case_key(task[1]), 5.0)
    for bucket in buckets:
        bucket.sort(key=ordinal.__getitem__)
    return buckets, loads


def partition_reports(expected, reports, processes_ok):
    rows = {name: [] for name in expected}
    owners = {}
    unexpected = []
    for name, keys in expected.items():
        for key in keys:
            if key in owners:
                raise ValueError("A required case is listed by multiple groups")
            owners[key] = name
    for report in reports:
        for case in report.iter("testcase"):
            key = case.get("classname", "") + "::" + case.get("name", "")
            if key not in owners:
                unexpected.append(key)
            else:
                rows[owners[key]].append(copy.deepcopy(case))
    passed = {}
    for name, cases in rows.items():
        keys = [case.get("classname", "") + "::" + case.get("name", "") for case in cases]
        passed[name] = (
            processes_ok
            and not unexpected
            and len(keys) == len(expected[name])
            and set(keys) == expected[name]
            and not any(
                case.find(tag) is not None
                for case in cases
                for tag in ("failure", "error", "skipped")
            )
        )
    return rows, passed, unexpected


def remaining_timeout(deadline):
    return None if deadline is None else max(0.001, deadline - time.monotonic())


def run_balanced_gate(
    root,
    destination,
    journeys,
    selected,
    count,
    started,
    run_ui,
    *,
    deadline=None,
    scheduler="case",
):
    """All collected cases execute once. Only mutable data is isolated per worker."""
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(root / "src"), str(root), environment.get("PYTHONPATH", "")]
    )
    environment["SYLVIA_QA_FIXTURE_ROOT"] = str(destination.resolve())
    environment.setdefault(
        "SRD_LITE_CANDIDATE",
        str(root / "tests/fixtures/SRD-Lite-117-QA-Legacy-Contract-Clean.sylvia-gm-package"),
    )
    temporary = destination / "temporary" / "shared-pool"
    temporary.mkdir(parents=True, exist_ok=True)
    for variable in ("TEMP", "TMP", "TMPDIR"):
        environment[variable] = str(temporary.resolve())
    expected = {}
    tasks = []
    required_keys = set()
    for name in selected:
        if name == "ui-controls":
            continue
        collection_started = time.monotonic()
        print(f"COLLECT {name}", flush=True)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--collect-only",
                "-q",
                "-o",
                "addopts=",
                "-o",
                f"cache_dir={destination / 'pytest-cache' / 'collection'}",
                *journeys[name],
            ],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=remaining_timeout(deadline),
            check=False,
        )
        nodeids = [
            line
            for line in result.stdout.splitlines()
            if line.startswith("tests/") and "::" in line
        ]
        if result.returncode or not nodeids:
            raise ValueError(
                "Required collection failed: " + (result.stdout + result.stderr)[-4000:]
            )
        keys = {case_key(nodeid) for nodeid in nodeids}
        collection_seconds = time.monotonic() - collection_started
        print(f"COLLECTED {name}: {len(nodeids)} cases in {collection_seconds:.2f}s", flush=True)
        with (destination / "collection-timings.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps({"group": name, "seconds": collection_seconds, "cases": len(nodeids)})
                + "\n"
            )
        if len(keys) != len(nodeids):
            raise ValueError("Required collection contains duplicate cases")
        if keys & required_keys:
            raise ValueError("A required case is listed by multiple groups")
        required_keys.update(keys)
        expected[name] = keys
        pairs = [key.split("::", 1) for key in sorted(keys)]
        (destination / f"{name}-required-cases.json").write_text(
            json.dumps(pairs, indent=2), encoding="utf-8"
        )
        tasks.extend((name, nodeid) for nodeid in nodeids)
    history = json.loads((root / "scripts/journey_case_timings.json").read_text())
    buckets, loads = balanced_assignments(
        tasks, count, history["seconds"], affinity=scheduler == "module"
    )
    (destination / "pool-assignments.json").write_text(
        json.dumps(
            {
                "workers": count,
                "scheduler": scheduler,
                "retain_successful_fixtures": environment.get("SYLVIA_RETAIN_SUCCESSFUL_FIXTURES")
                != "0",
                "estimated_seconds": loads,
                "tasks": buckets,
                "qualification_deadline_seconds": None if deadline is None else deadline - started,
                "timings_are_estimates_only": True,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    attempts = (destination / "attempts").resolve()
    run_id = hashlib.sha256(str(destination.resolve()).encode()).hexdigest()[:12]
    data_root = attempts / ("gate-" + run_id)
    if data_root.exists() or not data_root.resolve().is_relative_to(attempts):
        raise ValueError("Pool data must be a fresh contained owned directory")
    data_root.mkdir(parents=True)

    def run_worker(index, bucket):
        prefix = destination / f"shared-shard-{index}"
        xml = prefix.with_suffix(".xml")
        log = prefix.with_suffix(".log")
        data = data_root / f"shared-shard-{index}"
        args = [
            "-s",
            "-p",
            "scripts.journey_progress",
            "-o",
            f"cache_dir={destination / 'pytest-cache' / str(index)}",
            f"--junitxml={xml}",
            f"--basetemp={data}",
            *[nodeid for _, nodeid in bucket],
        ]
        if environment.get("SYLVIA_RETAIN_SUCCESSFUL_FIXTURES") == "0":
            args.extend(["-o", "tmp_path_retention_policy=failed"])
        argument_file = prefix.with_suffix(".arguments.json")
        argument_file.write_text(json.dumps(args), encoding="utf-8")
        launcher = (
            "import json,sys,pytest; "
            "raise SystemExit(pytest.main(json.load(open(sys.argv[1],encoding='utf-8'))))"
        )
        worker_environment = dict(environment)
        worker_environment["PYTHONPATH"] = os.pathsep.join(
            [
                str(__import__("pathlib").Path(__file__).resolve().parents[1]),
                str(root / "src"),
                worker_environment.get("PYTHONPATH", ""),
            ]
        )
        worker_environment["SYLVIA_JOURNEY_TIMINGS"] = str(
            prefix.with_name(prefix.name + "-timings.jsonl")
        )
        worker_environment["SYLVIA_JOURNEY_FIXTURE_TIMINGS"] = str(
            prefix.with_name(prefix.name + "-fixture-timings.jsonl")
        )
        worker_temp = temporary / str(index)
        worker_temp.mkdir()
        for variable in ("TEMP", "TMP", "TMPDIR"):
            worker_environment[variable] = str(worker_temp.resolve())
        print(f"RUN shared-shard-{index} ({len(bucket)} required cases)", flush=True)
        stopped = threading.Event()
        worker_started = time.monotonic()
        host_timings = prefix.with_name(prefix.name + "-host-timings.jsonl")

        def progress():
            position, current, current_started, heartbeat = (
                0,
                None,
                time.monotonic(),
                time.monotonic(),
            )
            first_case = True
            while True:
                done = stopped.wait(3)
                if log.exists():
                    with log.open(encoding="utf-8", errors="replace") as stream:
                        stream.seek(position)
                        lines = stream.readlines()
                        position = stream.tell()
                    for line in lines:
                        if line.startswith("JOURNEY "):
                            print(f"shard-{index} {line.strip()}", flush=True)
                            try:
                                row = json.loads(line[8:])
                                if row["event"] == "start":
                                    current, current_started = row["case"], time.monotonic()
                                    if first_case:
                                        with host_timings.open("a", encoding="utf-8") as timing:
                                            timing.write(
                                                json.dumps(
                                                    {
                                                        "phase": "launch_to_first_case",
                                                        "seconds": row.get(
                                                            "monotonic", current_started
                                                        )
                                                        - worker_started,
                                                        "includes": (
                                                            "process startup, pytest import "
                                                            "and worker collection"
                                                        ),
                                                    }
                                                )
                                                + "\n"
                                            )
                                        first_case = False
                                elif row.get("phase") == "teardown":
                                    current = None
                            except (ValueError, KeyError):
                                pass
                if current and time.monotonic() - heartbeat >= 60:
                    print(
                        f"PROGRESS shard-{index}: {current}; "
                        f"observed running {time.monotonic() - current_started:.1f}s",
                        flush=True,
                    )
                    heartbeat = time.monotonic()
                if done:
                    break

        observer = threading.Thread(target=progress, daemon=True)
        observer.start()
        try:
            with log.open("w", encoding="utf-8") as stream:
                result = subprocess.run(
                    [sys.executable, "-c", launcher, str(argument_file)],
                    cwd=root,
                    env=worker_environment,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=remaining_timeout(deadline),
                    check=False,
                )
            successful = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired) as error:
            with log.open("a", encoding="utf-8") as stream:
                stream.write(f"\n{type(error).__name__}: {error}")
            successful = False
        finally:
            stopped.set()
            observer.join()
        with host_timings.open("a", encoding="utf-8") as timing:
            timing.write(
                json.dumps(
                    {
                        "phase": "worker_wall",
                        "seconds": time.monotonic() - worker_started,
                        "successful": successful,
                    }
                )
                + "\n"
            )
        print(f"{'PASS' if successful else 'FAIL'} shared-shard-{index}", flush=True)
        return successful, xml, log

    # Rendered controls keep their existing deadlines without competing with heavy
    # Python fixture admission on the standard two-core Windows runner.
    ui_result = run_ui("ui-controls", destination) if "ui-controls" in selected else None
    with ThreadPoolExecutor(max_workers=count) as executor:
        pending = [
            executor.submit(run_worker, index, bucket)
            for index, bucket in enumerate(buckets)
            if bucket
        ]
        outcomes = [future.result() for future in pending]
    reports = []
    processes_ok = True
    for successful, xml, _log in outcomes:
        processes_ok &= successful
        if not xml.is_file():
            processes_ok = False
            continue
        try:
            reports.append(ET.parse(xml).getroot())
        except ET.ParseError:
            processes_ok = False
    rows, passed, unexpected = partition_reports(expected, reports, processes_ok)
    results = {}
    for name, cases in rows.items():
        report = ET.Element("testsuites")
        suite = ET.SubElement(report, "testsuite", name=name)
        for case in cases:
            suite.append(case)
        ET.ElementTree(report).write(destination / f"{name}.xml", encoding="utf-8")
        (destination / f"{name}.log").write_text(
            json.dumps(
                {
                    "execution": "shared balanced pool",
                    "passed": passed[name],
                    "expected": len(expected[name]),
                    "actual": len(cases),
                    "unexpected": unexpected,
                    "full_worker_logs": [str(log) for _, _, log in outcomes],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        results[name] = (passed[name], sum(float(case.get("time", 0)) for case in cases))
        print(
            f"{'PASS' if passed[name] else 'FAIL'} {name}: "
            f"{len(cases)}/{len(expected[name])} required cases",
            flush=True,
        )
    if ui_result:
        results["ui-controls"] = ui_result
    return results
