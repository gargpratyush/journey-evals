"""Where things live, depending on whether this is a checkout or an installed package.

Every path decision that differs between "cloned the repository" and "installed the wheel" is made
here, once. Installed, the tool is a guest in somebody else's project: its runs belong under their
working directory, its credentials come from their ``.env``, and the example application ships
inside the distribution because ``examples/`` is a source tree they never cloned.
"""

from pathlib import Path

from .feasibility import ARTIFACTS, ROOT

# A checkout has the build file and the source tree beside the package; an installed copy does not.
IN_CHECKOUT = (ROOT / "pyproject.toml").exists() and (ROOT / "journey_evals").is_dir()


def workspace():
    """The directory a run is launched from: the repository, or wherever the user is standing."""
    return ROOT if IN_CHECKOUT else Path.cwd()


def runs_root():
    """Where unnamed runs write their artifacts. ``--out`` always wins over this."""
    if IN_CHECKOUT:
        return ARTIFACTS / "runs"
    return Path.cwd() / "journey-evals" / "runs"


def work_root():
    """Scratch space for owned browser profiles, thrown away after every run."""
    if IN_CHECKOUT:
        return ARTIFACTS / "work"
    return Path.cwd() / "journey-evals" / "work"


def env_file():
    """The ``.env`` to read credentials from.

    The user's own project comes first, so an installed tool never silently picks up a key from
    somewhere they did not put one. Nothing here reads the file; that is `load_environment`, which
    only accepts known names and never interpolates.
    """
    local = Path.cwd() / ".env"
    if local.exists() or not (ROOT / ".env").exists():
        return local
    return ROOT / ".env"


def example_app():
    """The bundled synthetic application, from the source tree or from the installed copy."""
    try:
        from examples import flight_app
    except ModuleNotFoundError:
        from journey_evals._examples import flight_app
    return flight_app


def example_journeys():
    """The directory holding the bundled journey specifications."""
    if IN_CHECKOUT and (ROOT / "examples" / "journeys").is_dir():
        return ROOT / "examples" / "journeys"
    return Path(__file__).resolve().parent / "_examples" / "journeys"
