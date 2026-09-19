"use strict";

/**
 * Finding, and if necessary building, the Python environment this CLI runs in.
 *
 * Journey Evals drives a real browser from Python. The npm package does not reimplement it: it
 * ships the built wheel and installs it into a private virtual environment inside this package
 * directory, so nothing is added to the user's own interpreter and uninstalling the npm package
 * takes the whole thing with it.
 *
 * Every failure here is reported in full. A test tool that half-installed itself and then reported
 * a passing run would be worse than one that refused to start.
 */

const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const PACKAGE_ROOT = path.resolve(__dirname, "..");
const VENV = path.join(PACKAGE_ROOT, ".venv");
const STAMP = path.join(VENV, ".journey-evals");
const MINIMUM = [3, 12];

function venvPython() {
  return process.platform === "win32"
    ? path.join(VENV, "Scripts", "python.exe")
    : path.join(VENV, "bin", "python");
}

function wheel() {
  const vendor = path.join(PACKAGE_ROOT, "vendor");
  if (!fs.existsSync(vendor)) return null;
  const wheels = fs.readdirSync(vendor).filter((name) => name.endsWith(".whl")).sort();
  return wheels.length ? path.join(vendor, wheels[wheels.length - 1]) : null;
}

/** Interpreters worth trying, most specific first. `py` is the Windows launcher, not a program. */
function candidates() {
  const chosen = process.env.JOURNEY_EVALS_PYTHON;
  const explicit = chosen ? [{ command: chosen, args: [] }] : [];
  if (process.platform === "win32") {
    return [
      ...explicit,
      { command: "py", args: ["-3.13"] },
      { command: "py", args: ["-3.12"] },
      { command: "python", args: [] },
      { command: "python3", args: [] },
      // The Microsoft Store alias on PATH is a stub that installs nothing and answers nothing, so
      // a real interpreter in the usual per-user location is worth finding before giving up.
      ...windowsInstalls(),
    ];
  }
  return [
    ...explicit,
    { command: "python3.13", args: [] },
    { command: "python3.12", args: [] },
    { command: "python3", args: [] },
    { command: "python", args: [] },
  ];
}

function windowsInstalls() {
  const roots = [process.env.LOCALAPPDATA, process.env.ProgramFiles]
    .filter(Boolean)
    .map((base) => path.join(base, "Programs", "Python"))
    .concat([path.join(process.env.ProgramFiles || "C:\\Program Files", "Python")]);
  const found = [];
  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    for (const entry of fs.readdirSync(root).sort().reverse()) {
      const executable = path.join(root, entry, "python.exe");
      if (fs.existsSync(executable)) found.push({ command: executable, args: [] });
    }
  }
  return found;
}

function versionOf(candidate) {
  const probe = spawnSync(
    candidate.command,
    [...candidate.args, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
    { encoding: "utf8" }
  );
  if (probe.status !== 0 || !probe.stdout) return null;
  const parts = probe.stdout.trim().split(".").map(Number);
  return parts.length === 2 && parts.every(Number.isInteger) ? parts : null;
}

function findPython() {
  const seen = [];
  for (const candidate of candidates()) {
    const version = versionOf(candidate);
    if (!version) continue;
    seen.push(`${candidate.command} ${candidate.args.join(" ")} -> ${version.join(".")}`.trim());
    if (version[0] > MINIMUM[0] || (version[0] === MINIMUM[0] && version[1] >= MINIMUM[1])) {
      return { ...candidate, version };
    }
  }
  const found = seen.length ? `\nFound: ${seen.join(", ")}` : "";
  throw new Error(
    `Journey Evals needs Python ${MINIMUM.join(".")} or newer on PATH; it drives the browser from ` +
      `Python and ships that code as a wheel.${found}\n` +
      "Install it from https://www.python.org/downloads/, or point JOURNEY_EVALS_PYTHON at an " +
      "interpreter you already have, then run `npx journey-evals doctor`."
  );
}

function run(command, args, label) {
  const result = spawnSync(command, args, { stdio: "inherit" });
  if (result.error) throw new Error(`${label} could not start: ${result.error.message}`);
  if (result.status !== 0) throw new Error(`${label} failed with exit code ${result.status}`);
}

/** The wheel this environment was built from, or null if there is no usable environment. */
function installed() {
  if (!fs.existsSync(venvPython()) || !fs.existsSync(STAMP)) return null;
  return fs.readFileSync(STAMP, "utf8").trim();
}

/**
 * Make sure the private environment exists and matches the shipped wheel.
 *
 * Re-running is cheap and safe: if the stamp already names the wheel on disk, nothing happens.
 */
function ensureEnvironment({ quiet = false } = {}) {
  const distribution = wheel();
  if (!distribution) {
    throw new Error(
      "This package is missing its vendor/*.whl. It was probably built by hand; rebuild it with " +
        "`python scripts/build_npm.py` from the repository."
    );
  }
  const want = path.basename(distribution);
  if (installed() === want) return venvPython();

  const python = findPython();
  if (!quiet) {
    console.log(`journey-evals: using ${python.command} ${python.args.join(" ")}`.trim() +
      ` (Python ${python.version.join(".")})`);
    console.log(`journey-evals: creating a private environment in ${VENV}`);
  }
  if (!fs.existsSync(venvPython())) {
    run(python.command, [...python.args, "-m", "venv", VENV], "python -m venv");
  }
  if (!quiet) console.log(`journey-evals: installing ${want}`);
  run(venvPython(), ["-m", "pip", "install", "--quiet", "--upgrade", "pip"], "pip upgrade");
  run(venvPython(), ["-m", "pip", "install", "--quiet", distribution], `pip install ${want}`);
  fs.writeFileSync(STAMP, want + "\n", "utf8");
  if (!quiet) console.log("journey-evals: ready. Try `npx journey-evals demo`.");
  return venvPython();
}

module.exports = {
  PACKAGE_ROOT,
  VENV,
  MINIMUM,
  venvPython,
  wheel,
  findPython,
  installed,
  ensureEnvironment,
};
