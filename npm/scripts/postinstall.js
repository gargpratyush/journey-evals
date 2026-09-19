"use strict";

/**
 * Build the private Python environment at install time, so the first real command is fast.
 *
 * This never fails the install. `npm install` of an unrelated dependency tree should not break
 * because Python is missing from this machine; the CLI itself will refuse loudly, with the same
 * message, the moment somebody actually tries to run a journey.
 */

const { ensureEnvironment } = require("../lib/python");

if (process.env.JOURNEY_EVALS_SKIP_INSTALL === "1") {
  console.log("journey-evals: JOURNEY_EVALS_SKIP_INSTALL=1, skipping environment setup");
  process.exit(0);
}

try {
  ensureEnvironment({ quiet: false });
} catch (error) {
  console.warn("\njourney-evals: the Python environment was not created.");
  console.warn(error.message);
  console.warn("Install is otherwise complete. Run `npx journey-evals doctor` when ready.\n");
}
