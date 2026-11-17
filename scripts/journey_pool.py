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
from pathlib import Path


if __package__:
    from .check_catalog import qualification_cases
    from .known_test_defects import accepted_case
    from .platform_exclusions import EXCLUSIONS_FILE_VARIABLE, read_platform_exclusions
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.check_catalog import qualification_cases
    from scripts.known_test_defects import accepted_case
    from scripts.platform_exclusions import EXCLUSIONS_FILE_VARIABLE, read_platform_exclusions

RETAIN_VARIABLE = "SYLVIA_RETAIN_SUCCESSFUL_FIXTURES"


def retain_successful_fixtures(environment):
    """Whether passed tests keep their scratch; the answer is written back for the children.

    Keeping it is the explicit choice ``1``. Unset or ``0`` removes a passed test's scratch,
    because a full run would otherwise keep tens of gigabytes. Failed tests always keep theirs.
    """
    value = environment.get(RETAIN_VARIABLE) or "0"
    if value not in ("0", "1"):
        raise ValueError(f"{RETAIN_VARIABLE} must be 0 or 1, not {value!r}")
    environment[RETAIN_VARIABLE] = value
    return value == "1"


def retention_arguments(retain):
    """pytest options that remove a passed test's tmp_path and keep a failed test's."""
    return [] if retain else ["-o", "tmp_path_retention_policy=failed"]


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
            and all(accepted_case(case) for case in cases)
        )
    return rows, passed, unexpected


def report_failures(report):
    """One console line per case a JUnit report does not accept, so a failure is named."""
    lines = []
    for case in report.iter("testcase"):
        if accepted_case(case):
            continue
        outcome = next(
            (child for child in case if child.tag in ("failure", "error", "skipped")), None
        )
        kind = "unaccepted outcome" if outcome is None else outcome.tag
        message = "" if outcome is None else " ".join((outcome.get("message") or "").split())
        case_id = case.get("classname", "") + "::" + case.get("name", "")
        lines.append(f"FAILED CASE {case_id} [{kind}] {message[:400]}".rstrip())
    return lines


def remaining_timeout(deadline):
    return None if deadline is None else max(0.001, deadline - time.monotonic())


# The browser tests are one indivisible unit in a machine-share plan.
UI_SHARE_ITEM = ("ui-controls", "ui-controls::browser-tests")
UI_SHARE_SECONDS = 300.0


def parse_machine_share(text):
    """``I/N`` with 1 <= I <= N <= 64; anything else is an error for the caller to report."""
    index, separator, count = str(text).partition("/")
    if not separator or not index.isascii() or not count.isascii():
        raise ValueError(f"A machine share is written I/N, for example 2/6, not {text!r}")
    if not index.isdigit() or not count.isdigit():
        raise ValueError(f"A machine share is written I/N, for example 2/6, not {text!r}")
    index, count = int(index), int(count)
    if not 1 <= count <= 64 or not 1 <= index <= count:
        raise ValueError(f"A machine share needs 1 <= I <= N <= 64, not {text!r}")
    return index, count


def machine_share_plan(tasks, timings, count, *, include_ui):
    """Split the whole run into ``count`` shares that depend only on the cases and timings.

    Every machine at one commit computes the same plan: the cases are ordered by ID before
    balancing, so neither the collection order nor the local worker count can change it.
    The browser tests join as one item and land on a single share.
    """
    items = sorted(tasks, key=lambda task: (task[1], task[0]))
    estimates = timings
    if include_ui:
        items.append(UI_SHARE_ITEM)
        estimates = {**timings, case_key(UI_SHARE_ITEM[1]): UI_SHARE_SECONDS}
    buckets, loads = balanced_assignments(items, count, estimates)
    shares = []
    for bucket, load in zip(buckets, loads, strict=True):
        cases = [task for task in bucket if task != UI_SHARE_ITEM]
        shares.append({
            "tasks": cases,
            "ui_controls": UI_SHARE_ITEM in bucket,
            "estimated_seconds": round(load, 3),
            "longest_case_seconds": max(
                (estimates.get(case_key(nodeid), 5.0) for _group, nodeid in bucket), default=0.0
            ),
        })
    return shares


def required_cases_digest(nodeids):
    """One identity for the full required set, so shares from different sources cannot merge."""
    return hashlib.sha256("\n".join(sorted(nodeids)).encode("utf-8")).hexdigest()


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
    include_historical=False,
    machine_share=None,
    share_report=None,
    plan_only=False,
):
    """All collected cases execute once. Only mutable data is isolated per worker.

    ``machine_share`` is ``(index, count)``: this machine then runs only its share of the
    collected cases, and ``share_report`` (a dict) receives what the caller must record.
    ``plan_only`` collects, writes the plan into ``share_report`` and runs nothing.
    """
    environment = dict(os.environ)
    retain = retain_successful_fixtures(environment)
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTHONUNBUFFERED"] = "1"
    # Child text I/O must not depend on the machine's code page or UTF-8 mode.
    environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(root / "src"), str(root), environment.get("PYTHONPATH", "")]
    )
    environment["SYLVIA_QA_FIXTURE_ROOT"] = str(destination.resolve())
    environment["SYLVIA_RUNTIME_CACHE_DIR"] = str(destination.resolve() / "runtime-cache")
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
        # Collection records runs_on cases for other platforms; only it may write this file.
        platform_file = (destination / f"{name}-platform-exclusions.json").resolve()
        platform_file.unlink(missing_ok=True)
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
            env={**environment, EXCLUSIONS_FILE_VARIABLE: str(platform_file)},
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
        collected_count = len(nodeids)
        if len(set(nodeids)) != collected_count:
            raise ValueError("Required collection contains duplicate cases")
        nodeids, excluded = qualification_cases(
            nodeids, include_historical=include_historical,
            platform_exclusions=read_platform_exclusions(platform_file),
        )
        (destination / f"{name}-excluded-cases.json").write_text(
            json.dumps(excluded, indent=2), encoding="utf-8",
        )
        if not nodeids:
            raise ValueError("Required collection contains no product cases")
        keys = {case_key(nodeid) for nodeid in nodeids}
        collection_seconds = time.monotonic() - collection_started
        print(f"COLLECTED {name}: {len(nodeids)} cases in {collection_seconds:.2f}s", flush=True)
        with (destination / "collection-timings.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps({"group": name, "seconds": collection_seconds, "cases": len(nodeids),
                            "collected_cases": collected_count, "excluded_cases": len(excluded)})
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
    ui_selected = "ui-controls" in selected
    if machine_share is not None or plan_only:
        index, share_count = machine_share if machine_share is not None else (None, count)
        all_nodeids = sorted(nodeid for _group, nodeid in tasks)
        plan = machine_share_plan(tasks, history["seconds"], share_count, include_ui=ui_selected)
        if share_report is not None:
            share_report.update(
                count=share_count,
                required_cases_sha256=required_cases_digest(all_nodeids),
                total_required_cases=len(all_nodeids),
                shares=[
                    {"index": number, "cases": len(share["tasks"]),
                     "ui_controls": share["ui_controls"],
                     "estimated_seconds": share["estimated_seconds"],
                     "longest_case_seconds": share["longest_case_seconds"]}
                    for number, share in enumerate(plan, start=1)
                ],
            )
        if plan_only:
            return {}
        mine = plan[index - 1]
        tasks = mine["tasks"]
        ui_selected = mine["ui_controls"]
        assigned = {}
        for group, nodeid in tasks:
            assigned.setdefault(group, set()).add(case_key(nodeid))
        absent_groups = sorted(set(expected) - set(assigned))
        if "ui-controls" in selected and not ui_selected:
            absent_groups.append("ui-controls")
        # A group with no case in this share is not part of this machine's run.
        expected = assigned
        assigned_nodeids = sorted(nodeid for _group, nodeid in tasks)
        (destination / "machine-share.json").write_text(
            json.dumps(
                {
                    "index": index,
                    "count": share_count,
                    "required_cases_sha256": required_cases_digest(all_nodeids),
                    "required_cases": all_nodeids,
                    "assigned_cases": assigned_nodeids,
                    "ui_controls_included": ui_selected,
                    "absent_groups": absent_groups,
                    "estimated_seconds": [share["estimated_seconds"] for share in plan],
                    "timings_are_estimates_only": True,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        if share_report is not None:
            share_report.update(
                index=index, assigned_cases=len(assigned_nodeids),
                ui_controls_included=ui_selected, absent_groups=absent_groups,
            )
    buckets, loads = balanced_assignments(
        tasks, count, history["seconds"], affinity=scheduler == "module"
    )
    (destination / "pool-assignments.json").write_text(
        json.dumps(
            {
                "workers": count,
                "scheduler": scheduler,
                "retain_successful_fixtures": retain,
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
            *retention_arguments(retain),
            *[nodeid for _, nodeid in bucket],
        ]
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
    ui_result = run_ui("ui-controls", destination) if ui_selected else None
    with ThreadPoolExecutor(max_workers=count) as executor:
        pending = [
            executor.submit(run_worker, index, bucket)
            for index, bucket in enumerate(buckets)
            if bucket
        ]
        outcomes = [future.result() for future in pending]
    reports = []
    processes_ok = True
    for successful, xml, log in outcomes:
        processes_ok &= successful
        if not xml.is_file():
            processes_ok = False
            print(f"FAIL {log.stem} wrote no report; read {log.name}", flush=True)
            continue
        try:
            report = ET.parse(xml).getroot()
        except ET.ParseError:
            processes_ok = False
            print(f"FAIL {log.stem} wrote a malformed report; read {log.name}", flush=True)
            continue
        reports.append(report)
        failures = report_failures(report)
        for line in failures:
            print(f"{line} (full output in {log.name})", flush=True)
        if not successful and not failures:
            print(f"FAIL {log.stem} exited with no failed case in its report; "
                  f"read {log.name}", flush=True)
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
