#!/usr/bin/env node
"use strict";

/**
 * The npm entry point. It is a shim and nothing more.
 *
 * Arguments are passed through untouched and the exit code is returned verbatim, because the exit
 * code is part of the contract: 0 pass, 1 fail, 2 inconclusive, 3 error, 4 configuration refused.
 * Rewriting it here — for instance turning "inconclusive" into a pass — would quietly break every
 * CI job that trusts it.
 */

const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const { ensureEnvironment, venvPython, wheel, findPython, installed, VENV } = require("../lib/python");

const CONFIGURATION_EXIT = 4;

function doctor() {
  console.log("journey-evals doctor");
  const distribution = wheel();
  console.log(`  package      ${path.resolve(__dirname, "..")}`);
  console.log(`  wheel        ${distribution ? path.basename(distribution) : "MISSING"}`);
  try {
    const python = findPython();
    const shown = `${python.command} ${python.args.join(" ")}`.trim();
    console.log(`  python       ${shown} (${python.version.join(".")})`);
  } catch (error) {
    console.log(`  python       NOT FOUND\n${error.message}`);
    return CONFIGURATION_EXIT;
  }
  console.log(`  environment  ${fs.existsSync(venvPython()) ? VENV : "not created yet"}`);
  console.log(`  installed    ${installed() || "nothing yet"}`);
  const key = process.env.JEV_API_KEY || process.env.TYPESAFE_API_KEY;
  const dotenv = fs.existsSync(path.join(process.cwd(), ".env"));
  // Never print the key. Whether one is present is the only thing worth saying out loud.
  console.log(`  credentials  ${key ? "JEV_API_KEY set in this environment" : dotenv ? ".env found in this directory" : "none found (set JEV_API_KEY or write a .env)"}`);
  console.log("\nRun `npx journey-evals demo` to watch a real browser drive a bundled example.");
  return 0;
}

function main() {
  const argv = process.argv.slice(2);
  if (argv[0] === "doctor") process.exit(doctor());
  try {
    ensureEnvironment({ quiet: true });
  } catch (error) {
    console.error(`journey-evals: ${error.message}`);
    process.exit(CONFIGURATION_EXIT);
  }
  const result = spawnSync(venvPython(), ["-m", "journey_evals.cli", ...argv], { stdio: "inherit" });
  if (result.error) {
    console.error(`journey-evals: could not start the run: ${result.error.message}`);
    process.exit(CONFIGURATION_EXIT);
  }
  // A signal is not an exit code. Say so rather than inventing a verdict for a killed run.
  if (result.signal) {
    console.error(`journey-evals: the run was terminated by ${result.signal}`);
    process.exit(3);
  }
  process.exit(result.status === null ? 3 : result.status);
}

main();
