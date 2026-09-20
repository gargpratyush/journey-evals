"""The command line surface: one command that runs a journey and writes one evidence-backed report.

Configuration is fully resolved and validated in this process, before a browser exists. A journey
that contradicts its command line arguments, or that cannot state how success would be verified,
exits without mutating anything.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .contracts import (
    CONFIGURATION_EXIT,
    SCHEMA_VERSION,
    ContractError,
    effective_spec,
    exit_code,
    viewport_class,
)
from .feasibility import SLOW_MACHINE_TIMEOUT, write_json
from .isolation import OS_ENV
from .paths import example_app, example_journeys, runs_root, workspace
from .report import terminal_summary, write_agent_feedback, write_junit
from .runner import TOOL_VERSION

# A worker is never killed and restarted mid-journey, so this bound only has to be generous.
WORKER_TIMEOUT = 900 + 4 * SLOW_MACHINE_TIMEOUT
CHECKPOINTS = (
    "preflight", "verify-browser", "verify-clean", "screen-detection", "repair-prepare",
    "repair-verify", "verify-repair-loop", "screen-reliability", "live-runs", "measure-overhead",
    "second-app",
)


def _viewport(value):
    """One or more named presets or explicit sizes. Resolved to pixels here, once.

    Accepts ``mobile``, ``mobile,desktop`` and ``[mobile, desktop]``, and the flag may be repeated.
    The bracket form is allowed because it is how a journey file writes the same thing.
    """
    from .contracts import ContractError, resolve_viewport

    text = value.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    parts = [part for part in (item.strip() for item in text.split(",")) if part]
    if not parts:
        raise argparse.ArgumentTypeError("--viewport needs at least one size")
    try:
        return [resolve_viewport(part) for part in parts]
    except ContractError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def _requested_viewports(args):
    """Flatten a repeatable, comma-separated --viewport into resolved sizes, in the order given."""
    requested = []
    for group in getattr(args, "viewport", None) or ():
        for pair in group:
            if pair not in requested:
                requested.append(pair)
    return requested


def _viewport_help():
    from .contracts import VIEWPORT_PRESETS, viewport_names

    named = ", ".join(f"{name} ({VIEWPORT_PRESETS[name][0]}x{VIEWPORT_PRESETS[name][1]})"
                      for name in viewport_names())
    return (f"Which declared size(s) to run. Repeatable, or comma separated. "
            f"Defaults to every size the journey declares. A size like 1120x780, or one of: {named}")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="journey-evals",
        description="Run one declared user journey in a real browser and report what actually happened.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run one journey and write a report")
    run.add_argument("--url", help="Where the journey starts")
    run.add_argument("--task", help="What a person is trying to accomplish, in plain language")
    run.add_argument("--journey", help="Path to a journey specification with declared checks")
    run.add_argument("--mode", choices=("explore", "verify"))
    run.add_argument("--viewport", type=_viewport, metavar="SIZE", action="append",
                     help=_viewport_help())
    run.add_argument("--out", default=None, help="Directory for this run's artifacts")
    run.add_argument("--warn-as-error", action="store_true",
                     help="Treat advisory findings as a failing exit code")
    run.add_argument("--blocking-check", action="append", default=[],
                     help="Promote one declared check from advisory to blocking")
    run.add_argument("--headed", action="store_true", help="Show the owned browser window")
    run.add_argument("--quiet", action="store_true")

    validate = sub.add_parser("validate", help="Check a journey specification without running it")
    validate.add_argument("--journey", required=True)

    serve = sub.add_parser("serve", help="Serve the bundled synthetic example application")
    serve.add_argument("--app", default="flight", choices=("flight", "subscription", "admin"))
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--fault", default="none")

    show = sub.add_parser("show", help="Print a summary of a previous run directory")
    show.add_argument("directory")

    init = sub.add_parser("init", help="Copy the bundled example journeys into this project")
    init.add_argument("--dir", default="journeys", help="Where to write them (default: ./journeys)")
    init.add_argument("--force", action="store_true", help="Overwrite files that already exist")

    demo = sub.add_parser("demo", help="Watch a bundled journey against the bundled example app")
    demo.add_argument("--journey", default="flight-booking",
                      help="Name of a bundled journey (see `journey-evals demo --list`)")
    demo.add_argument("--list", action="store_true", help="List the bundled journeys and stop")
    demo.add_argument("--fault", default="none", help="Seed a defect into the example app")
    demo.add_argument("--port", type=int, default=8770, help="Console port on 127.0.0.1")
    demo.add_argument("--viewport", type=_viewport, metavar="SIZE", action="append",
                      help=_viewport_help())
    demo.add_argument("--headless", action="store_true")
    demo.add_argument("--no-open", action="store_true")

    browser = sub.add_parser("install-browser",
                             help="Download the pinned Chrome for Testing build this tool runs")
    browser.add_argument("--force", action="store_true", help="Re-download even if it is present")

    watch = sub.add_parser("watch", help="Run one journey and watch it live in a local console")
    watch.add_argument("--url", help="Where the journey starts")
    watch.add_argument("--task", help="What a person is trying to accomplish, in plain language")
    watch.add_argument("--journey", help="Path to a journey specification with declared checks")
    watch.add_argument("--mode", choices=("explore", "verify"))
    watch.add_argument("--viewport", type=_viewport, metavar="SIZE", action="append",
                       help=_viewport_help())
    watch.add_argument("--out", default=None, help="Directory for this run's artifacts")
    watch.add_argument("--blocking-check", action="append", default=[],
                       help="Promote one declared check from advisory to blocking")
    watch.add_argument("--port", type=int, default=8770, help="Console port on 127.0.0.1")
    watch.add_argument("--serve-app", choices=("flight", "subscription", "admin"),
                       help="Also serve the bundled example app on the journey's own port")
    watch.add_argument("--fault", default="none", help="Seed a defect into the served example app")
    watch.add_argument("--headless", action="store_true",
                       help="Hide the browser window and watch only the console")
    watch.add_argument("--start", action="store_true",
                       help=argparse.SUPPRESS)  # accepted and ignored: watching always starts
    watch.add_argument("--no-open", action="store_true", help="Do not open a browser tab")

    agent = sub.add_parser("agent", help="Evaluate a LangGraph agent from its observable trace")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    agent_run = agent_sub.add_parser("run", help="Run one agent evaluation and write a report")
    agent_run.add_argument("--eval", required=True, help="Path to an agent evaluation specification")
    agent_run.add_argument("--out", default=None, help="Directory for this run's artifacts")
    agent_run.add_argument("--quiet", action="store_true")
    agent_run.add_argument("--warn-as-error", action="store_true",
                           help="Treat advisory judge findings as a failing exit code")
    agent_run.add_argument("--watch", action="store_true",
                           help="Print each message, tool call and judge verdict as it is recorded")
    agent_run.add_argument("--show-cost", "--showCost", dest="show_cost", action="store_true",
                           help="Report what the judge calls cost when the run finishes")
    agent_validate = agent_sub.add_parser("validate", help="Validate an agent evaluation specification")
    agent_validate.add_argument("--eval", required=True)
    agent_console = agent_sub.add_parser(
        "console", help="Watch an agent evaluation in a browser: the conversation, then the judging")
    agent_console.add_argument("--eval", required=True,
                               help="Path to an agent evaluation specification")
    agent_console.add_argument("--out", default=None, help="Directory for this run's artifacts")
    agent_console.add_argument("--port", type=int, default=0,
                               help="Port for the local viewer; 0 picks a free one")
    agent_console.add_argument("--no-open", action="store_true",
                               help="Do not open a browser automatically")
    agent_chat = agent_sub.add_parser(
        "chat", help="Talk to an agent in the terminal, without evaluating it")
    agent_chat.add_argument("--entrypoint",
                            help="The agent as module:attribute, e.g. examples.my_agent:graph")
    agent_chat.add_argument("--eval",
                            help="Take the entrypoint from an evaluation specification instead")
    agent_chat.add_argument("--hide-tools", action="store_true",
                            help="Show only replies, not the tool calls behind them")

    for name in CHECKPOINTS:
        checkpoint = sub.add_parser(name, help="Feasibility campaign checkpoint (see plans/jev-feasibility)")
        checkpoint.add_argument("rest", nargs=argparse.REMAINDER)
    return parser


def _resolve(args, viewport=None):
    """Turn command line input into one immutable specification, or fail before launching."""
    spec, provenance = effective_spec(
        journey_file=args.journey, url=args.url, task=args.task, mode=args.mode,
        viewport=viewport or (_requested_viewports(args) or [None])[0],
        source=args.journey or "<cli>",
    )
    return spec, provenance


def _planned_viewports(args):
    """Decide which sizes this invocation runs, before anything is spent.

    A journey file declares the matrix. Nothing requested runs all of it; a request must name
    sizes the journey declares. Without a journey file there is nothing to select from, so a
    single requested size is simply the size.
    """
    from .contracts import declared_viewports, select_viewports

    requested = _requested_viewports(args)
    if not args.journey:
        return requested[:1] or [None]
    return list(select_viewports(declared_viewports(args.journey), requested))


def command_validate(args):
    try:
        spec, provenance = effective_spec(journey_file=args.journey, source=args.journey)
    except (ContractError, OSError) as error:
        print(f"invalid journey: {error}", file=sys.stderr)
        return CONFIGURATION_EXIT
    print(f"{spec.id}: {len(spec.checks)} check(s), {len(spec.probes)} probe(s), mode {spec.mode}")
    if len(spec.viewports) > 1:
        from .contracts import viewport_label

        sizes = ", ".join(f"{viewport_label(pair)} ({pair[0]}x{pair[1]})" for pair in spec.viewports)
        print(f"viewport matrix: {len(spec.viewports)} runs - {sizes}")
    print(f"effective_spec_sha256 {provenance['effective_spec_sha256']}")
    unreferenced = [c.id for c in spec.checks
                    if not any(c.id in p.required_checks for p in spec.probes)]
    if not spec.probes:
        # Without probes nothing forces the run to reach the states these checks live in, so a
        # journey that never got there would look clean rather than incomplete.
        print("no probes declared: checks not required by any probe cannot force coverage")
    if unreferenced:
        print("checks not required by any probe: " + ", ".join(unreferenced))
    return 0


def _write_error_report(directory, spec, provenance, detail, started):
    """A crashed run still has to say so in the same shape as any other run.

    Leaving the directory without a report means whatever reads these artifacts has to guess, and
    the safe guess is not always the one that gets made. An ERROR report states plainly that
    nothing was established: no check resolved, and the goal status is unavailable rather than
    violated, because a tool that fell over has not shown the application is broken either.
    """
    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": directory.name,
        "tool_version": TOOL_VERSION,
        "journey_id": spec.id,
        "journey": provenance,
        "effective_spec_sha256": provenance.get("sha256"),
        "result": "ERROR",
        "result_basis": [{"check_id": None, "effect": "error", "policy": "execution_failure_v1"}],
        "execution_status": "error",
        "goal_status": "unavailable",
        "goal_evidence": [{"criterion": "execution", "state": "unknown", "detail": detail}],
        "coverage": {"checks": {check.id: "pending" for check in spec.checks},
                     "check_evidence": {}, "probes": {}, "missing_required_checks": [],
                     "failed_required_checks": [], "unvisited_probes": [], "complete": False,
                     "steps": 0, "observations": 0, "acceptance": []},
        "findings": [], "evaluations": [], "observations": [], "history": [],
        "errors": [{"stage": "execution", "detail": detail}],
        "usage": {"model_requests": 0, "actor_requests": 0, "evaluator_requests": 0,
                  "input_tokens": 0, "output_tokens": 0, "usd": "0.000000"},
        "timings": {"total_ms": round((time.perf_counter() - started) * 1000)},
        "environment": {}, "artifacts": {}, "model_versions": {},
        "acceptance": spec.acceptance,
        "limits": ["This run did not complete. Nothing about the application was established."],
    }
    write_json(directory / "report.json", payload)
    write_junit(directory, payload)
    write_agent_feedback(directory, payload)
    return payload


def command_run(args):
    try:
        viewports = _planned_viewports(args)
    except (ContractError, OSError) as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return CONFIGURATION_EXIT
    if len(viewports) == 1:
        return _run_one(args, viewports[0], args.out)
    return _run_matrix(args, viewports)


def _run_matrix(args, viewports):
    """Run the same declared journey once per size, and report each result on its own terms.

    Each size is an independent measurement written to its own directory. A size is never skipped
    because an earlier one failed, and the results are never merged: a journey that passes on a
    desktop and fails on a phone has found a real defect, and collapsing that into one verdict
    would hide exactly what the matrix was declared to find.
    """
    from .contracts import viewport_label

    root = Path(args.out) if args.out else runs_root() / f"matrix-{uuid.uuid4().hex[:12]}"
    root.mkdir(parents=True, exist_ok=True)
    labels = ", ".join(viewport_label(pair) for pair in viewports)
    if not args.quiet:
        print(f"running {len(viewports)} viewport(s): {labels}\n")
    cells, worst = [], 0
    for pair in viewports:
        label = viewport_label(pair)
        if not args.quiet:
            print(f"--- {label} ({pair[0]}x{pair[1]})")
        directory = root / label
        code = _run_one(args, pair, directory)
        worst = max(worst, code)
        report = directory / "report.json"
        payload = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
        cells.append({
            "viewport": {"width": pair[0], "height": pair[1]}, "label": label,
            "directory": str(directory), "result": payload.get("result", "ERROR"),
            "goal_status": payload.get("goal_status", "unavailable"),
            "execution_status": payload.get("execution_status", "error"),
            "failed_required_checks": payload.get("coverage", {}).get("failed_required_checks", []),
            "findings": [finding.get("evaluator") for finding in payload.get("findings", [])],
            "exit_code": code,
        })
    summary = {"schema_version": SCHEMA_VERSION, "journey": args.journey, "viewports": cells,
               "result": max(cells, key=lambda cell: cell["exit_code"])["result"],
               "exit_code": worst}
    write_json(root / "matrix.json", summary)
    if not args.quiet:
        print(_matrix_summary(cells))
        print(f"\nartifacts: {root}")
    return worst


def _matrix_summary(cells):
    width = max(len(cell["label"]) for cell in cells)
    lines = ["", "Viewport matrix"]
    for cell in cells:
        size = f"{cell['viewport']['width']}x{cell['viewport']['height']}"
        detail = ", ".join(cell["failed_required_checks"]) or cell["goal_status"]
        lines.append(f"  {cell['label']:<{width}}  {size:>9}  {cell['result']:<12} {detail}")
    return "\n".join(lines)


def _run_one(args, viewport, out):
    try:
        spec, provenance = _resolve(args, viewport)
    except (ContractError, OSError) as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return CONFIGURATION_EXIT
    directory = Path(out) if out else runs_root() / f"{spec.id}-{uuid.uuid4().hex[:12]}"
    directory.mkdir(parents=True, exist_ok=True)
    existing = directory / "events.jsonl"
    if existing.exists() and existing.stat().st_size:
        print(f"configuration error: {directory} already holds a recorded run. Every measured "
              f"interval is read back out of that journal, so a second run written beside it "
              f"would be timed against the first. Choose an empty --out directory.",
              file=sys.stderr)
        return CONFIGURATION_EXIT
    write_json(directory / "effective-spec.json", provenance)
    command = [sys.executable, "-m", "journey_evals.runner",
               "--spec", str(directory / "effective-spec.json"), "--out", str(directory)]
    for check in args.blocking_check:
        command += ["--blocking-check", check]
    if args.headed:
        command.append("--headed")
    if args.warn_as_error:
        command.append("--warn-as-error")
    # Credentials are read from .env by the worker itself and never handed down through the
    # environment that also reaches the browser process.
    environment = {k: v for k, v in os.environ.items() if k.upper() in OS_ENV}
    started = time.perf_counter()
    try:
        process = subprocess.run(command, cwd=workspace(), env=environment, timeout=WORKER_TIMEOUT,
                                 capture_output=True, text=True)
    except subprocess.TimeoutExpired as expired:
        (directory / "worker-output.txt").write_text(str(expired), encoding="utf-8")
        _write_error_report(directory, spec, provenance,
                            f"the run exceeded the {WORKER_TIMEOUT}s wall clock", started)
        print(f"run exceeded {WORKER_TIMEOUT}s; artifacts in {directory}", file=sys.stderr)
        return 3
    (directory / "worker-output.txt").write_text(process.stdout + process.stderr, encoding="utf-8")
    report = directory / "report.json"
    if not report.exists():
        print(process.stdout + process.stderr, file=sys.stderr)
        _write_error_report(directory, spec, provenance,
                            "the run ended before any report could be written", started)
        print(f"the run ended without a report; artifacts in {directory}", file=sys.stderr)
        return 3
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload.setdefault("timings", {})["cli_wall_ms"] = round((time.perf_counter() - started) * 1000)
    if not args.quiet:
        print(terminal_summary(payload, journey_task=spec.task))
        print(f"\nartifacts: {directory}")
    return exit_code(payload["result"], warn_as_error=args.warn_as_error)


def command_serve(args):
    from . import console

    flight_app = example_app()

    if args.port and console.port_is_taken(args.port):
        print(f"something is already listening on 127.0.0.1:{args.port}. Stop it first, or pick "
              f"another --port; two servers on one port would let a run read another process's "
              f"records.", file=sys.stderr)
        return CONFIGURATION_EXIT
    server = flight_app.build(port=args.port, fault=args.fault, app=args.app)
    host, port = server.server_address[0], server.server_address[1]
    print(f"serving the synthetic {args.app} application on http://{host}:{port} (fault: {args.fault})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0


def command_show(args):
    path = Path(args.directory)
    report = path if path.is_file() else path / "report.json"
    if not report.exists():
        print(f"no report at {report}", file=sys.stderr)
        return CONFIGURATION_EXIT
    payload = json.loads(report.read_text(encoding="utf-8"))
    task = payload.get("journey", {}).get("resolved", {}).get("task", "")
    if payload.get("runtime", {}).get("type") == "agent":
        from .agent_evals import agent_terminal_summary

        print(agent_terminal_summary(payload, task=task))
    else:
        print(terminal_summary(payload, journey_task=task))
    return 0


def command_agent_validate(args):
    from .agent_evals import load_agent_spec

    try:
        spec = load_agent_spec(args.eval)
    except (ContractError, OSError) as error:
        print(f"invalid agent evaluation: {error}", file=sys.stderr)
        return CONFIGURATION_EXIT
    print(f"{spec.id}: {spec.framework}, {len(spec.judges)} judge(s)")
    print(f"effective_spec_sha256 {spec.sha256}")
    if not spec.acceptance:
        print("no independent acceptance declared: this evaluation cannot produce PASS")
    return 0


def command_agent_run(args):
    from .agent_evals import (
        agent_terminal_summary,
        cost_summary,
        live_printer,
        load_agent_spec,
        run_agent_eval,
    )
    from .feasibility import load_environment
    from .paths import env_file

    try:
        spec = load_agent_spec(args.eval)
        try:
            # The agent under test usually needs its own provider credentials, whether or not a
            # judge runs. A missing Jev key only matters when a judge was actually declared.
            load_environment(env_file())
        except ValueError:
            if spec.judges:
                raise
        directory = Path(args.out) if args.out else runs_root() / f"{spec.id}-{uuid.uuid4().hex[:12]}"
        secrets = [
            value for name, value in os.environ.items()
            if name.endswith(("_API_KEY", "_KEY", "_TOKEN", "_SECRET")) and value
        ]
        result = run_agent_eval(spec, directory, secrets=secrets,
                                observer=live_printer() if args.watch else None)
    except (ContractError, OSError, ValueError) as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return CONFIGURATION_EXIT
    if not args.quiet:
        if args.watch:
            print("")
        print(agent_terminal_summary(result, task=spec.task))
        if args.show_cost:
            print(cost_summary(result))
        print(f"\nartifacts: {directory}")
    return exit_code(result["result"], warn_as_error=args.warn_as_error)


def command_agent_console(args):
    """Watch an agent evaluation in a browser: the conversation, then the judging."""
    from .agent_console import AgentSession, serve
    from .agent_evals import load_agent_spec
    from .feasibility import load_environment
    from .paths import env_file

    try:
        spec = load_agent_spec(args.eval)
        try:
            load_environment(env_file())
        except ValueError:
            if spec.judges:
                raise
        directory = Path(args.out) if args.out else runs_root() / f"{spec.id}-{uuid.uuid4().hex[:12]}"
        session = AgentSession(spec, directory)
    except (ContractError, OSError, ValueError) as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return CONFIGURATION_EXIT
    code = serve(session, port=args.port, open_browser=not args.no_open)
    print(f"artifacts: {directory}")
    return code


def command_agent_chat(args):
    """Talk to an agent directly. Nothing is judged or recorded; this is for exploring behaviour."""
    from .agent_evals import agent_chat, load_agent_spec
    from .feasibility import load_environment
    from .paths import env_file

    try:
        if bool(args.entrypoint) == bool(args.eval):
            raise ContractError("pass exactly one of --entrypoint or --eval")
        entrypoint = args.entrypoint or load_agent_spec(args.eval).entrypoint
        try:
            load_environment(env_file())
        except ValueError:
            pass  # the agent brings its own credentials; no judge runs in a chat session
        return agent_chat(entrypoint, show_tools=not args.hide_tools)
    except (ContractError, OSError, ValueError) as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return CONFIGURATION_EXIT


def command_init(args):
    """Put the example journeys where a new project can read, run and edit them.

    Nothing is overwritten without being asked: a journey you have edited is your file, and a tool
    that silently replaced it would be destroying the very thing it is meant to help you write.
    """
    import shutil

    source = example_journeys()
    target = Path(args.dir)
    target.mkdir(parents=True, exist_ok=True)
    written, skipped = [], []
    for journey in sorted(source.glob("*.json")):
        destination = target / journey.name
        if destination.exists() and not args.force:
            skipped.append(journey.name)
            continue
        shutil.copyfile(journey, destination)
        written.append(journey.name)
    for name in written:
        print(f"wrote {target / name}")
    for name in skipped:
        print(f"kept your {target / name} (use --force to overwrite)")
    print("\nNext:")
    print("  1. put JEV_API_KEY=... in a .env file beside this directory, or in your environment")
    print("  2. journey-evals validate --journey " + str(target / "flight-booking.json"))
    print("  3. journey-evals demo            # watch a journey against the bundled example app")
    return 0


def command_install_browser(args):
    """Fetch the pinned browser explicitly, so nothing large is ever downloaded behind your back."""
    from .browsers import BROWSER_VERSION, install

    try:
        install(BROWSER_VERSION, force=args.force)
    except Exception as error:  # noqa: BLE001 - the reason matters more than the type here
        print(f"could not install the pinned browser: {error}", file=sys.stderr)
        return CONFIGURATION_EXIT
    return 0


# Which bundled application each bundled journey is declared against. The journeys are trusted
# input, so this is an explicit table rather than a guess made from the name.
EXAMPLE_APPS = {
    "flight-booking": "flight",
    "flight-booking-back": "flight",
    "flight-booking-matrix": "flight",
    "flight-booking-narrow": "flight",
    "workspace-subscription": "subscription",
    "plan-downgrade": "admin",
    "workspace-deletion": "admin",
    "revenue-freshness": "admin",
}


def example_app_for(name):
    return EXAMPLE_APPS.get(name, "flight")


def command_demo(args):
    """One command that needs no journey file, no application and no arguments to be worth running.

    It is the bundled synthetic application, a bundled journey, and the live console, so that the
    first thing anybody sees is a real browser being driven and a real report being written.
    """
    names = sorted(path.stem for path in example_journeys().glob("*.json"))
    if args.list:
        print("bundled journeys:")
        for name in names:
            print(f"  {name:<24} {example_app_for(name)} application")
        return 0
    journey = example_journeys() / f"{args.journey}.json"
    if not journey.exists():
        print(f"no bundled journey called {args.journey!r}; available: {', '.join(names)}",
              file=sys.stderr)
        return CONFIGURATION_EXIT
    application = example_app_for(args.journey)
    argv = ["watch", "--journey", str(journey), "--serve-app", application,
            "--fault", args.fault, "--port", str(args.port)]
    for group in getattr(args, "viewport", None) or ():
        for pair in group:
            argv += ["--viewport", f"{pair[0]}x{pair[1]}"]
    if args.headless:
        argv.append("--headless")
    if args.no_open:
        argv.append("--no-open")
    return main(argv)


def command_watch(args):
    """Run a journey at each declared viewport in turn, and render the journal live.

    The viewer never judges anything. With several viewports the cells run one after another so
    there is always exactly one run on screen; a finished cell becomes a summary you can reopen.
    """
    from urllib.parse import urlparse as _urlparse

    from . import console
    from .contracts import viewport_label

    try:
        viewports = _planned_viewports(args)
        specs = [_resolve(args, pair)[0] for pair in viewports]
    except (ContractError, OSError) as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return CONFIGURATION_EXIT
    spec = specs[0]

    fixture = None
    if args.serve_app:
        flight_app = example_app()

        port = _urlparse(spec.url).port or 0
        if port and console.port_is_taken(port):
            print(f"something is already listening on 127.0.0.1:{port}. Stop it first, or drop "
                  f"--serve-app and serve the fixture yourself; two servers on one port would let "
                  f"this run read another process's records.", file=sys.stderr)
            return CONFIGURATION_EXIT
        try:
            fixture = flight_app.build(port=port, fault=args.fault, app=args.serve_app)
        except OSError as error:
            print(f"could not serve the example app on port {port}: {error}", file=sys.stderr)
            return CONFIGURATION_EXIT
        threading.Thread(target=fixture.serve_forever, daemon=True).start()
        print(f"serving the synthetic {args.serve_app} application on {spec.url} "
              f"(fault: {args.fault})")

    root = Path(args.out) if args.out else runs_root() / f"{spec.id}-{uuid.uuid4().hex[:12]}"
    environment = {k: v for k, v in os.environ.items() if k.upper() in OS_ENV}
    cells = []
    for cell_spec in specs:
        label = viewport_label(cell_spec.viewport)
        directory = root if len(specs) == 1 else root / label
        command = console.worker_command(
            journey=args.journey, url=args.url, task=args.task, mode=args.mode,
            viewport=cell_spec.viewport, out=directory, headed=not args.headless,
            blocking_checks=args.blocking_check,
        )
        # The worker reads its own credentials from .env; the console hands down nothing extra.
        cells.append({
            "label": label, "viewport": tuple(cell_spec.viewport),
            "viewport_class": viewport_class(cell_spec.viewport),
            "session": console.Session(directory, command, environment=environment, cwd=workspace()),
        })
    session = console.MatrixSession(cells)
    if len(specs) > 1:
        print(f"watching {len(specs)} viewport(s) in turn: "
              f"{', '.join(cell['label'] for cell in cells)}")
    try:
        return console.serve(session, port=args.port, open_browser=not args.no_open,
                             journey={"id": spec.id, "task": spec.task, "url": spec.url,
                                      "checks": len(spec.checks), "artifacts": str(root),
                                      "viewport": list(spec.viewport),
                                      "viewport_class": viewport_class(spec.viewport),
                                      "viewports": [{"label": cell["label"],
                                                     "viewport": list(cell["viewport"]),
                                                     "viewport_class": cell["viewport_class"]}
                                                    for cell in cells]})
    finally:
        if fixture is not None:
            fixture.shutdown()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in CHECKPOINTS:
        # The feasibility campaign keeps its own argument surface; delegate rather than restate it.
        from .feasibility import main as feasibility_main

        previous = sys.argv
        sys.argv = ["journey-evals", *argv]
        try:
            return feasibility_main()
        finally:
            sys.argv = previous
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return command_run(args)
    if args.command == "validate":
        return command_validate(args)
    if args.command == "serve":
        return command_serve(args)
    if args.command == "show":
        return command_show(args)
    if args.command == "init":
        return command_init(args)
    if args.command == "demo":
        return command_demo(args)
    if args.command == "install-browser":
        return command_install_browser(args)
    if args.command == "watch":
        return command_watch(args)
    if args.command == "agent":
        if args.agent_command == "run":
            return command_agent_run(args)
        if args.agent_command == "validate":
            return command_agent_validate(args)
        if args.agent_command == "chat":
            return command_agent_chat(args)
        if args.agent_command == "console":
            return command_agent_console(args)
    parser.error(f"unknown command {args.command}")
    return CONFIGURATION_EXIT


if __name__ == "__main__":
    sys.exit(main())
