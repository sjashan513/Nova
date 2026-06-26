"""
Terminal primitive tool -- no LLM involved.

Real subprocess execution, gated by a command whitelist. This is the
genuine security boundary the original docs (nova_technical_overview.md)
mention but never define: "A safety whitelist prevents dangerous
commands from executing regardless of what the model generates." This
module is where that whitelist actually exists.

Contract (revised in the Fase 2 design session, after T8 found a real
incompatibility with the original string-command design): `run` takes
a separate `command` (the base executable/subcommand, e.g. "cat",
"git status") and `args` (a list of arguments). The whitelist checks
ONLY `command`, against a fixed set of exact strings -- never against
`args`. This is what lets a step's args contain a "$step_id.field"
reference resolved by core/director/context.py: context.py's reference
syntax deliberately requires a value to be ENTIRELY a reference (no
partial interpolation, sealed in T3 of this same session) -- splitting
command from args means each arg can independently be a full reference
or a full literal, never a mix, without ever needing the whitelist to
inspect a resolved, dynamic value.

The OLD design (a single command string, whitelisted by leading-token
prefix) could not express "cat <path resolved from an earlier step>"
at all: "cat $s1.path" is not a complete reference (context.py would
not resolve it, by design) and "$s1.path" alone never matches any
whitelist prefix. This redesign is the fix -- see T8's closure test
session for how the incompatibility was found.

subprocess.run() is called with shell=False (the default) and a list
of args, never a single string passed through a shell -- this avoids
shell injection entirely (no `;`, `&&`, backticks, or `$()` can escape
into a second command), independent of and in addition to the
whitelist.
"""

import subprocess
from typing import Any, Dict, List, Optional

# Allow-list by exact command string. Simpler and safer than the old
# prefix-matching approach: since `command` is never combined with
# `args` into one string before this check runs, there is no token-
# boundary ambiguity to worry about (the old design's defense against
# "git statusrm" sneaking past "git status" is no longer needed --
# `command` is compared whole, not as a prefix of something longer).
# Grows only when a real, concrete use case needs a new command -- not
# speculatively.
ALLOWED_COMMANDS: List[str] = [
    "cat",
    "ls",
    "tsc",
    "npm",
    "pytest",
    "git",
]

DEFAULT_TIMEOUT_SECONDS = 30


class CommandNotAllowedError(Exception):
    """
    Raised when `command` is not in ALLOWED_COMMANDS. Deliberately a
    plain Exception, not a NovaError subclass -- this is caught and
    wrapped into StepExecutionError by core/director/error_policy.py
    exactly like any other execution-time failure, the same way a real
    FileNotFoundError from tools/filesystem.py would be. Keeping it out
    of the NovaError hierarchy avoids a security-relevant rejection
    being mistaken for a contract-validation error (PlanContractError),
    which is a different category detected at a different point in the
    pipeline (before execution, not during).
    """


def run(
    command: str,
    args: Optional[List[str]] = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """
    Runs `command` with `args` if and only if `command` is exactly in
    ALLOWED_COMMANDS. Raises CommandNotAllowedError before subprocess
    is ever touched if it does not match. `args` is NEVER checked
    against the whitelist -- each element may be a literal value or a
    fully-resolved "$step_id.field" reference (resolved by
    core/director/context.py before this function is called), and
    either way the whitelist has nothing to say about it; only the
    base command is gated.

    Returns {"command": str, "args": List[str], "stdout": str,
    "stderr": str, "exit_code": int} -- exit_code != 0 is NOT raised as
    an exception; a failing test run or a non-zero tsc exit code is a
    normal, successful EXECUTION of this tool that produced a result
    indicating failure, not a failure of the tool call itself. Whether
    a non-zero exit_code should fail the Plan is a decision for
    whatever step consumes this result, not for this function.

    Raises:
        CommandNotAllowedError: `command` is not in ALLOWED_COMMANDS.
        subprocess.TimeoutExpired: execution exceeded `timeout` seconds.
    """
    args = args or []

    if command not in ALLOWED_COMMANDS:
        raise CommandNotAllowedError(
            f"Command not allowed: '{command}'. Allowed commands: "
            f"{ALLOWED_COMMANDS}"
        )

    result = subprocess.run(
        [command, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )

    return {
        "command": command,
        "args": args,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.returncode,
    }
