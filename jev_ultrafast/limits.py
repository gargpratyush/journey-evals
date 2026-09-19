"""Limits the browser itself imposes, kept free of imports so reading them starts nothing.

browser.py connects to Browser Harness at import time, so anything that only wants these numbers
must be able to read them without that happening.
"""

VIEWPORT = (1120, 780)
# A fresh headless renderer can take seconds to produce its first frame on a contended workstation,
# and every frame-dependent CDP call blocks until it does. Measured spikes reached ~10s, so bound
# startup and shutdown generously: the replies do arrive, and a real hang still fails the run.
SLOW_MACHINE_TIMEOUT = 60
