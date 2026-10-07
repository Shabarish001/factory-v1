"""Capability classification for the Factory command runner."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable


class PolicyLevel(str, Enum):
    READ_ONLY = "READ_ONLY"
    BUILD_AND_TEST = "BUILD_AND_TEST"
    DEPENDENCY_CHANGE = "DEPENDENCY_CHANGE"
    PROJECT_WRITE = "PROJECT_WRITE"
    EXTERNAL_ACTION = "EXTERNAL_ACTION"
    DESTRUCTIVE = "DESTRUCTIVE"


POLICY_RANK = {
    PolicyLevel.READ_ONLY: 1,
    PolicyLevel.BUILD_AND_TEST: 2,
    PolicyLevel.DEPENDENCY_CHANGE: 3,
    PolicyLevel.PROJECT_WRITE: 4,
    PolicyLevel.EXTERNAL_ACTION: 5,
    PolicyLevel.DESTRUCTIVE: 6,
}

SHELL_SYNTAX = {";", "&&", "||", "|", ">", ">>", "<", "<<"}
READ_ONLY_GIT_COMMANDS = {
    "branch",
    "diff",
    "log",
    "ls-files",
    "rev-parse",
    "show",
    "status",
    "tag",
}
VERSION_COMMANDS = {"--version", "-v", "version"}
PACKAGE_MANAGERS = {"npm", "pnpm", "yarn"}
SAFE_PACKAGE_SCRIPTS = {
    "build",
    "check",
    "format:check",
    "lint",
    "test",
    "test:e2e",
    "typecheck",
}
DEPENDENCY_SUBCOMMANDS = {
    "add",
    "ci",
    "install",
    "remove",
    "uninstall",
    "update",
    "upgrade",
}
PACKAGE_MANAGER_LOCATION_FLAGS = {
    "-c",
    "--cwd",
    "--dir",
    "--prefix",
}
GLOBAL_INSTALL_FLAGS = {"-g", "--global", "--location=global"}
EXTERNAL_EXECUTABLES = {
    "curl",
    "firebase",
    "gh",
    "glab",
    "vercel",
    "wget",
}
DESTRUCTIVE_GIT_SUBCOMMANDS = {"clean", "reset"}
DESTRUCTIVE_EXECUTABLES = {
    "del",
    "format",
    "rmdir",
    "rm",
    "shutdown",
}


@dataclass(frozen=True)
class PolicyDecision:
    level: PolicyLevel
    allowed_by_default: bool
    reason: str
    command_family: str


def _executable(command: Iterable[str]) -> str:
    first = next(iter(command), "")
    return Path(first.replace("\\", "/")).name.lower().removesuffix(".exe").removesuffix(".cmd")


def _contains_path_escape(command: tuple[str, ...]) -> bool:
    for index, token in enumerate(command):
        normalized = token.replace("\\", "/")
        lowered = normalized.lower()
        path_switches = {"-c", "--work-tree", "--git-dir", "--prefix", "--cwd", "--dir"}
        if lowered in path_switches or any(
            lowered.startswith(f"{option}=") for option in path_switches if option != "-c"
        ):
            return True
        if normalized.startswith("../") or normalized == "..":
            return True
    return False


class CommandPolicy:
    """Classify argv without executing it or invoking a shell."""

    def __init__(self, allowed_policies: Iterable[str] | None = None, safe_script_names: Iterable[str] | None = None):
        self.allowed_policies = {
            PolicyLevel(value) for value in (allowed_policies or [PolicyLevel.READ_ONLY.value, PolicyLevel.BUILD_AND_TEST.value])
        }
        self.safe_script_names = set(safe_script_names or SAFE_PACKAGE_SCRIPTS)

    def classify(self, command: Iterable[str], available_scripts: Iterable[str] = ()) -> PolicyDecision:
        argv = tuple(command)
        if not argv:
            return PolicyDecision(PolicyLevel.PROJECT_WRITE, False, "Command cannot be empty", "invalid")
        if any(token in SHELL_SYNTAX for token in argv):
            return PolicyDecision(
                PolicyLevel.DESTRUCTIVE,
                False,
                "Shell syntax is not accepted; pass executable arguments separately",
                "shell-syntax",
            )
        if _contains_path_escape(argv):
            return PolicyDecision(
                PolicyLevel.DESTRUCTIVE,
                False,
                "Command contains an explicit path outside the selected repository",
                "path-boundary",
            )

        executable = _executable(argv)
        args = list(argv[1:])
        if executable in DESTRUCTIVE_EXECUTABLES:
            return PolicyDecision(PolicyLevel.DESTRUCTIVE, False, "Destructive executable is blocked", executable)
        if executable in EXTERNAL_EXECUTABLES:
            return PolicyDecision(PolicyLevel.EXTERNAL_ACTION, False, "External action is disabled", executable)

        if executable == "git":
            subcommand = next((arg for arg in args if not arg.startswith("-")), "")
            if subcommand in DESTRUCTIVE_GIT_SUBCOMMANDS:
                return PolicyDecision(PolicyLevel.DESTRUCTIVE, False, f"git {subcommand} is destructive", "git")
            if subcommand in {"push", "fetch", "pull", "clone", "remote"}:
                return PolicyDecision(PolicyLevel.EXTERNAL_ACTION, False, f"git {subcommand} is disabled", "git")
            if subcommand in {"apply", "am", "checkout", "commit", "merge", "mv", "rebase", "revert", "rm", "switch"}:
                return PolicyDecision(PolicyLevel.PROJECT_WRITE, False, f"git {subcommand} may modify the project", "git")
            if subcommand in READ_ONLY_GIT_COMMANDS:
                return PolicyDecision(PolicyLevel.READ_ONLY, True, f"git {subcommand} reads repository state", "git")
            return PolicyDecision(PolicyLevel.PROJECT_WRITE, False, f"git {subcommand} is not classified as read-only", "git")

        if executable in PACKAGE_MANAGERS:
            return self._classify_package_manager(executable, args, set(available_scripts))
        if executable == "npx":
            return self._classify_npx(args)
        if executable in {"node", "python", "python3", "pytest", "cargo", "go"}:
            return self._classify_tool(executable, args)
        return PolicyDecision(PolicyLevel.PROJECT_WRITE, False, "Executable is not in the controlled command policy", executable)

    def _classify_package_manager(self, executable: str, args: list[str], available_scripts: set[str]) -> PolicyDecision:
        if not args or args[0] in VERSION_COMMANDS:
            return PolicyDecision(PolicyLevel.READ_ONLY, True, f"{executable} version check is read-only", executable)
        subcommand = args[0]
        if subcommand in DEPENDENCY_SUBCOMMANDS:
            lowered_args = {argument.lower() for argument in args[1:]}
            if lowered_args.intersection(GLOBAL_INSTALL_FLAGS) or any(
                argument.lower().startswith("--location=global") for argument in args[1:]
            ) or any(
                argument.lower() == "--location"
                and index + 1 < len(args)
                and args[index + 1].lower() == "global"
                for index, argument in enumerate(args[1:], start=1)
            ):
                return PolicyDecision(
                    PolicyLevel.DEPENDENCY_CHANGE,
                    False,
                    "Global package installation is not authorized",
                    executable,
                )
            if any(
                argument.lower() in PACKAGE_MANAGER_LOCATION_FLAGS
                or any(
                    argument.lower().startswith(f"{flag}=")
                    for flag in PACKAGE_MANAGER_LOCATION_FLAGS
                    if flag != "-c"
                )
                for argument in args[1:]
            ):
                return PolicyDecision(
                    PolicyLevel.DEPENDENCY_CHANGE,
                    False,
                    "Package-manager working-directory overrides are blocked",
                    executable,
                )
            if any(
                argument.replace("\\", "/").lower().startswith(("file:", "link:", "./"))
                or Path(argument).is_absolute()
                for argument in args[1:]
            ):
                return PolicyDecision(
                    PolicyLevel.DEPENDENCY_CHANGE,
                    False,
                    "Local dependency paths are blocked by the controlled runner",
                    executable,
                )
            return PolicyDecision(
                PolicyLevel.DEPENDENCY_CHANGE,
                PolicyLevel.DEPENDENCY_CHANGE in self.allowed_policies,
                f"{executable} {subcommand} may change dependency metadata",
                executable,
            )
        script = subcommand
        if subcommand in {"run", "exec"} and len(args) > 1:
            script = args[1]
        if subcommand in {"test", "lint", "build", "check", "typecheck"}:
            script = subcommand
        if subcommand in {"run", "test", "lint", "build", "check", "typecheck"}:
            if script in self.safe_script_names and (not available_scripts or script in available_scripts):
                return PolicyDecision(PolicyLevel.BUILD_AND_TEST, True, f"{executable} runs approved script {script}", executable)
            return PolicyDecision(PolicyLevel.PROJECT_WRITE, False, f"Script {script} is not approved for build/test execution", executable)
        if subcommand in {"config", "list", "why", "info"}:
            return PolicyDecision(PolicyLevel.READ_ONLY, True, f"{executable} {subcommand} inspects package metadata", executable)
        return PolicyDecision(PolicyLevel.PROJECT_WRITE, False, f"{executable} {subcommand} is not classified", executable)

    def _classify_npx(self, args: list[str]) -> PolicyDecision:
        if "--no-install" not in args:
            return PolicyDecision(PolicyLevel.DEPENDENCY_CHANGE, False, "npx must use --no-install to prevent implicit installation", "npx")
        remaining = [arg for arg in args if arg != "--no-install"]
        if remaining[:2] in (["playwright", "test"], ["vitest"]):
            return PolicyDecision(PolicyLevel.BUILD_AND_TEST, True, "npx runs a locally installed test tool", "npx")
        return PolicyDecision(PolicyLevel.PROJECT_WRITE, False, "npx command is not approved", "npx")

    def _classify_tool(self, executable: str, args: list[str]) -> PolicyDecision:
        if not args or args[0] in VERSION_COMMANDS:
            return PolicyDecision(PolicyLevel.READ_ONLY, True, f"{executable} version check is read-only", executable)
        if executable in {"pytest", "cargo", "go"} and args[0] in {"test", "check"}:
            return PolicyDecision(PolicyLevel.BUILD_AND_TEST, True, f"{executable} test/check creates only development artifacts", executable)
        if executable in {"python", "python3"} and args[:2] == ["-m", "pytest"]:
            return PolicyDecision(PolicyLevel.BUILD_AND_TEST, True, "Python pytest execution is a build/test operation", executable)
        if executable in {"python", "python3"} and args[:2] == ["-m", "unittest"]:
            return PolicyDecision(PolicyLevel.BUILD_AND_TEST, True, "Python unittest execution is a build/test operation", executable)
        if executable == "node" and args and args[0] in {"--version", "-v"}:
            return PolicyDecision(PolicyLevel.READ_ONLY, True, "Node version check is read-only", executable)
        return PolicyDecision(PolicyLevel.PROJECT_WRITE, False, f"{executable} invocation is not classified", executable)


def policy_allows(decision: PolicyDecision, requested: PolicyLevel, allowed_policies: set[PolicyLevel]) -> tuple[bool, str]:
    """Apply requested and configured policy gates to a classification."""

    if decision.level in {PolicyLevel.EXTERNAL_ACTION, PolicyLevel.DESTRUCTIVE, PolicyLevel.PROJECT_WRITE}:
        return False, decision.reason
    if not decision.allowed_by_default:
        return False, decision.reason
    if decision.level not in allowed_policies:
        return False, f"Policy level {decision.level.value} is disabled by configuration"
    if POLICY_RANK[decision.level] > POLICY_RANK[requested]:
        return False, f"Command classified as {decision.level.value}, above requested {requested.value}"
    return True, decision.reason
