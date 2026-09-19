"""Explicit, locally persisted feasibility checkpoints; no inference in ordinary tests."""

import argparse
import hashlib
import json
import os
import platform
import shlex
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from importlib.metadata import version
from pathlib import Path

MODEL = "jev-1.13.0"
# The browser's own limits live with the browser: jev_ultrafast is the imported agent and does not
# depend on this package. They are read from a module that imports nothing, so reading a number
# never connects to a harness, and are re-exported because the campaign has always read them here.
from jev_ultrafast.limits import SLOW_MACHINE_TIMEOUT, VIEWPORT  # noqa: E402,F401

# The declared response deadline for a journey's final action. Evidence captured before this has
# elapsed cannot distinguish "the effect never happened" from "the effect had not happened yet".
SETTLE_MS = 1200
ENDPOINT = "https://api.typesafe.ai/v1/systemone"
RATE = Decimal("0.042") / 1_000_000
# Single source of truth for the cumulative campaign cap. The ledger refuses to change an
# existing budget.json implicitly, so raising this also requires deliberately editing that
# file's limit_usd. Keep the exact string: Decimal("0.40") serialises as "0.40", not "0.4".
CAP = "0.40"
MAX_REQUEST_TOKENS = 65_536
ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "feasibility"
ENV_KEYS = {
    "JEV_API_KEY", "TYPESAFE_API_KEY", "TYPESAFE_MODEL",
    "TEXT_MODEL_API_KEY", "TEXT_MODEL_BASE_URL", "TEXT_MODEL",
    "GPT_LUNA_API_ENDPOINT", "GPT_LUNA_API_KEY",
}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_environment(path):
    """Read only model settings; never execute/interpolate dotenv content."""
    if Path(path).exists():
        for number, raw in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            name, separator, value = raw.removeprefix("export ").partition("=")
            if not separator:
                raise ValueError(f"Invalid env assignment on line {number}; value omitted")
            name = name.strip()
            if name not in ENV_KEYS:
                continue
            try:
                parts = shlex.split(value, comments=True, posix=True)
            except ValueError:
                raise ValueError(f"Invalid env quoting on line {number}; value omitted") from None
            if len(parts) > 1:
                raise ValueError(f"Quote multiword env values on line {number}; value omitted")
            os.environ.setdefault(name, parts[0] if parts else "")
    first, second = os.environ.get("TYPESAFE_API_KEY"), os.environ.get("JEV_API_KEY")
    if first and second and first != second:
        raise ValueError("Conflicting Jev credentials; choose one configuration")
    if not (first or second):
        raise ValueError("Configure JEV_API_KEY or TYPESAFE_API_KEY in the local env file")
    configured_model = os.environ.get("TYPESAFE_MODEL", MODEL)
    if configured_model != MODEL:
        raise ValueError(f"This campaign pins {MODEL}; a different model needs a new baseline")
    os.environ["TYPESAFE_API_KEY"] = first or second
    os.environ["TYPESAFE_MODEL"] = MODEL
    return first or second


class Budget:
    """Single-process campaign ledger with persistent reservations before each HTTP attempt."""

    def __init__(self, path, limit_usd, *, max_attempts=120, prior_receipt=None, secrets=()):
        self.path = Path(path)
        try:
            self.limit = Decimal(str(limit_usd))
        except InvalidOperation:
            raise ValueError("Budget must be a finite positive USD amount") from None
        if not self.limit.is_finite() or self.limit <= 0:
            raise ValueError("Budget must be a finite positive USD amount")
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("Attempt limit must be a positive integer")
        self.max_attempts = max_attempts
        self.secret_bytes = [secret.encode() for secret in secrets if secret]
        self.prior_receipt = prior_receipt
        self.lock = self.path.with_suffix(".lock")
        self.started = {}
        self.sent = 0

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise RuntimeError("Budget ledger is locked; inspect its owner before removing a stale lock") from None
        with os.fdopen(descriptor, "w") as stream:
            stream.write(str(os.getpid()))
        try:
            if self.path.exists():
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
                if self.data["limit_usd"] != str(self.limit):
                    raise ValueError("Campaign cap cannot change implicitly; review the existing ledger")
            else:
                historical_tokens = 0
                source_hash = None
                if self.prior_receipt:
                    prior = Path(self.prior_receipt).read_bytes()
                    attempts = json.loads(prior)["http_attempts"]["attempts"]
                    if any(x["status"] != 200 or "usage" not in x for x in attempts):
                        raise ValueError("Historical receipt has ambiguous usage; reconcile it before import")
                    historical_tokens = sum(x["usage"]["input_tokens"] for x in attempts)
                    if type(historical_tokens) is not int or historical_tokens < 0:
                        raise ValueError("Invalid historical token usage")
                    source_hash = hashlib.sha256(prior).hexdigest()
                self.data = {
                    "schema_version": 1, "model": MODEL, "limit_usd": str(self.limit),
                    "input_token_rate_usd": str(RATE), "historical_tokens": historical_tokens,
                    "historical_receipt_sha256": source_hash, "attempts": [],
                }
                write_json(self.path, self.data)
            if self.data["model"] != MODEL or self.data["input_token_rate_usd"] != str(RATE):
                raise ValueError("Ledger model/pricing differs from this campaign")
            return self
        except Exception:
            self.lock.unlink()
            raise

    def __exit__(self, *_args):
        self.lock.unlink()

    @property
    def accounted_tokens(self):
        return self.data["historical_tokens"] + sum(x["accounted_tokens"] for x in self.data["attempts"])

    def reserve(self, request):
        if str(request.url) != ENDPOINT:
            raise ValueError("This campaign authorizes only the TypeSafe evaluation endpoint")
        if any(secret in request.content for secret in self.secret_bytes):
            raise ValueError("Credential material in inference payload; request rejected")
        if len(request.content) > 60_000 or json.loads(request.content).get("model") != MODEL:
            raise ValueError("Request exceeds the campaign size or pinned-model boundary")
        if self.sent >= self.max_attempts:
            raise RuntimeError("Checkpoint HTTP-attempt limit reached")
        if (self.accounted_tokens + MAX_REQUEST_TOKENS) * RATE > self.limit:
            raise RuntimeError("Campaign spending cap would be exceeded; no request sent")
        index = len(self.data["attempts"])
        request.extensions["jev_budget_index"] = index
        self.started[index] = time.perf_counter()
        self.data["attempts"].append({
            "id": index, "status": "reserved", "accounted_tokens": MAX_REQUEST_TOKENS,
            "request_sha256": hashlib.sha256(request.content).hexdigest(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        write_json(self.path, self.data)
        self.sent += 1

    def reconcile(self, response):
        index = response.request.extensions["jev_budget_index"]
        entry = self.data["attempts"][index]
        entry["status"] = response.status_code
        entry["latency_ms"] = round((time.perf_counter() - self.started[index]) * 1000)
        response.read()
        if response.status_code == 200:
            body = response.json()
            usage = body.get("usage", {})
            tokens = usage.get("input_tokens")
            if type(tokens) is not int or tokens < 0:
                write_json(self.path, self.data)
                raise ValueError("Missing valid token usage; conservative reservation retained")
            entry.update(accounted_tokens=tokens, usage=usage, model=body.get("model"))
            write_json(self.path, self.data)
            if tokens > MAX_REQUEST_TOKENS or body.get("model") != MODEL:
                raise ValueError("Provider response violated the pinned model/context contract")
        else:
            write_json(self.path, self.data)

    def receipt(self):
        return {
            "limit_usd": str(self.limit), "attempts_this_checkpoint": self.sent,
            "accounted_input_tokens_including_history": self.accounted_tokens,
            "accounted_usd": str(self.accounted_tokens * RATE),
            "billing": "Published-rate estimate; non-success/unknown requests retain maximum reservation.",
        }


def checkpoint_receipt(name, result, root=ARTIFACTS):
    result = dict(result, schema_version=1, checkpoint=name, timestamp=datetime.now(timezone.utc).isoformat())
    result["attempt_id"] = uuid.uuid4().hex
    write_json(Path(root) / "checkpoints" / "attempts" / f"{name}-{result['attempt_id']}.json", result)
    write_json(Path(root) / "checkpoints" / f"{name}.json", result)
    return result


def preflight(args):
    from jev_ultrafast import model

    key = load_environment(args.env_file)
    result = {
        "status": "RUNNING", "model": MODEL, "upstream_revision": "452c1ad2dd628008f1d5608f28158d76e49e6cc0",
        "privacy": "Only synthetic text to TypeSafe; keys stay server-side; local artifacts; no production access.",
        "target_scope": "Owned loopback fixture only; no real transaction endpoints.",
        "python": platform.python_version(),
        "packages": {name: version(name) for name in ("browser-harness", "httpx")},
        "capabilities": {
            "jev_configured": True,
            "helper_configured": all(os.environ.get(name) for name in (
                "TEXT_MODEL_API_KEY", "TEXT_MODEL_BASE_URL", "TEXT_MODEL",
            )),
            "helper_enabled": False, "vision_enabled": False,
        },
    }
    budget = Budget(
        ARTIFACTS / "budget.json", args.jev_budget_usd, max_attempts=3,
        prior_receipt=args.prior_receipt, secrets=(key,),
    )
    original_hooks = model.CLIENT.event_hooks
    try:
        with budget:
            model.CLIENT.event_hooks = {"request": [budget.reserve], "response": [budget.reconcile]}
            response = model.post_json(ENDPOINT, key, {
                "model": MODEL,
                "state": {"visible_message": "Synthetic booking confirmed.", "continue_button_enabled": True},
                "questions": {
                    "notice": {
                        "type": "choice", "instructions": "What does the visible message say?",
                        "criteria": {
                            "confirmed": "Booking confirmed", "failed": "Booking failed", "unknown": "Unclear",
                        },
                    },
                    "control": {
                        "type": "choice", "instructions": "Is the continue button enabled?",
                        "criteria": {"enabled": "Enabled", "disabled": "Disabled"},
                    },
                },
            })
            notice = model.validate_choice(response["answers"]["notice"], {"confirmed", "failed", "unknown"})
            control = model.validate_choice(response["answers"]["control"], {"enabled", "disabled"})
            if notice["choice"] != "confirmed" or control["choice"] != "enabled":
                raise ValueError("Synthetic typed-answer smoke did not match its known state")
            result.update(status="GREEN", returned_model=response["model"], typed_answers_validated=2)
    except (ValueError, RuntimeError, OSError) as error:
        result.update(status="AMBER", error=str(error).replace(key, "[REDACTED]"))
    finally:
        model.CLIENT.event_hooks = original_hooks
    if hasattr(budget, "data"):
        result["budget"] = budget.receipt()
    result = checkpoint_receipt("cp0", result)
    print(json.dumps(result, indent=2))
    return int(result["status"] != "GREEN")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    smoke = commands.add_parser("preflight", help="CP0: explicit bounded live access smoke")
    smoke.add_argument("--env-file", type=Path, default=ROOT / ".env")
    smoke.add_argument("--jev-budget-usd", required=True)
    smoke.add_argument("--prior-receipt", type=Path)
    commands.add_parser("verify-browser", help="CP1: offline browser/evidence invariants")
    clean = commands.add_parser("verify-clean", help="CP2: three predeclared live clean journeys")
    clean.add_argument("--jev-budget-usd", required=True)
    detection = commands.add_parser("screen-detection", help="CP3: frozen screening corpus and live fare-control trio")
    detection.add_argument("--jev-budget-usd", required=True)
    inject = commands.add_parser("repair-prepare", help="CP4: inject one defect into a disposable app copy")
    inject.add_argument("--defect", required=True)
    repair = commands.add_parser("repair-verify", help="CP4: check one repair against the frozen criterion")
    repair.add_argument("--defect", required=True)
    repair.add_argument("--attempt", type=int, default=1)
    commands.add_parser("verify-repair-loop", help="CP4: gate both repaired targets")
    commands.add_parser("screen-reliability", help="CP5-A: frozen holdout across three arms")
    live = commands.add_parser("live-runs", help="CP5-B: 60 randomized live journeys")
    live.add_argument("--seed", type=int, default=20260918)
    commands.add_parser("measure-overhead", help="CP5-D: matched scripted pairs, no model calls")
    second = commands.add_parser("second-app", help="CP6: 20 declared cases on the second application")
    second.add_argument("--seed", type=int, default=20260919)
    args = parser.parse_args()
    try:
        if args.command == "preflight":
            return preflight(args)
        if args.command == "screen-detection":
            from .screening import screening
            return screening(args.jev_budget_usd)
        if args.command == "screen-reliability":
            from .reliability import screen_quality
            return screen_quality()
        if args.command == "live-runs":
            from .live import live_checkpoint
            return live_checkpoint(args.seed)
        if args.command == "measure-overhead":
            from .overhead import overhead_checkpoint
            return overhead_checkpoint()
        if args.command == "second-app":
            from .subscription import second_app_checkpoint
            return second_app_checkpoint(args.seed)
        if args.command.startswith("repair-") or args.command == "verify-repair-loop":
            from .repair import prepare, repair_checkpoint, verify_repair
            if args.command == "repair-prepare":
                return prepare(args.defect)
            if args.command == "repair-verify":
                return verify_repair(args.defect, args.attempt)
            return repair_checkpoint()
        from .browser_checks import browser_checkpoint, clean_checkpoint
        if args.command == "verify-browser":
            return browser_checkpoint()
        return clean_checkpoint(args.jev_budget_usd)
    except (Exception, KeyboardInterrupt) as error:
        from .browser_checks import source_fingerprint

        message = f"{type(error).__name__}: {error}"
        for name in ("JEV_API_KEY", "TYPESAFE_API_KEY", "TEXT_MODEL_API_KEY"):
            if os.environ.get(name):
                message = message.replace(os.environ[name], "[REDACTED]")
        name = {"preflight": "cp0", "verify-browser": "cp1", "verify-clean": "cp2", "screen-detection": "cp3",
                "repair-prepare": "cp4", "repair-verify": "cp4", "verify-repair-loop": "cp4",
                "screen-reliability": "cp5", "live-runs": "cp5", "measure-overhead": "cp5",
                "second-app": "cp6"}[args.command]
        result = checkpoint_receipt(name, {
            "status": "AMBER", "source_sha256": source_fingerprint(), "error": message,
        })
        print(json.dumps(result, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
