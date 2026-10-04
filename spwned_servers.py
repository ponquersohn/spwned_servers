#!/usr/bin/env python3
"""
Interactive builder for chained MSSQL linked-server queries.

You build up a "route" one step at a time:

    linked_server DB01        add a hop -> local -> DB01
    impersonate_login sa      impersonate (EXECUTE AS LOGIN) at the CURRENT frame
    impersonate_user dbo      impersonate (EXECUTE AS USER) at the CURRENT frame
    linked_server DB02        add another hop -> local -> DB01 -> DB02
    back                      undo the last step you issued
    route                     print the current route
    help                      show help
    exit / quit               leave

Anything else you type is treated as a SQL statement. It is wrapped through
every hop/impersonation currently on the route and the resulting query is
printed (never executed).

The "current frame" that impersonate_login/impersonate_user applies to is
always whatever the most recent linked_server step put you on (or `local`
if you haven't added a linked_server yet). Issuing impersonate_* again on
the same frame overwrites the previous impersonation for that frame.
"""

import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import Optional, Tuple, List


class Colors:
    """ANSI color codes, toggled off by --no-color, NO_COLOR, or a non-tty."""

    enabled = True

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"

    @classmethod
    def wrap(cls, text: str, *codes: str) -> str:
        if not cls.enabled:
            return text
        return "".join(codes) + text + cls.RESET

    @classmethod
    def configure(cls, no_color_flag: bool):
        cls.enabled = (
            not no_color_flag
            and os.environ.get("NO_COLOR") is None
            and sys.stdout.isatty()
        )


def c_hop(text: str) -> str:
    """Linked server hop name."""
    return Colors.wrap(text, Colors.CYAN, Colors.BOLD)


def c_local(text: str) -> str:
    return Colors.wrap(text, Colors.GRAY)


def c_imp(text: str) -> str:
    """Impersonation annotation."""
    return Colors.wrap(text, Colors.MAGENTA)


def c_arrow(text: str) -> str:
    return Colors.wrap(text, Colors.GRAY)


def c_ok(text: str) -> str:
    return Colors.wrap(text, Colors.GREEN)


def c_err(text: str) -> str:
    return Colors.wrap(text, Colors.RED, Colors.BOLD)


def c_query(text: str) -> str:
    return Colors.wrap(text, Colors.YELLOW)


def c_prompt(text: str) -> str:
    return Colors.wrap(text, Colors.BLUE, Colors.BOLD)


def c_dim(text: str) -> str:
    return Colors.wrap(text, Colors.DIM)


def sql_quote(value: str) -> str:
    """Escape SQL Server single quotes inside a string literal."""
    return value.replace("'", "''")


@dataclass
class Frame:
    # name is None for the local/starting frame
    name: Optional[str]
    # ("LOGIN", value) or ("USER", value) or None
    impersonate: Optional[Tuple[str, str]] = None


@dataclass
class Step:
    kind: str          # "server" or "impersonate"
    name: Optional[str] = None     # for "server"
    imp_kind: Optional[str] = None  # "LOGIN" / "USER" for "impersonate"
    imp_value: Optional[str] = None


class RouteState:
    def __init__(self):
        self.steps: List[Step] = []

    # ---- mutation ----

    def add_server(self, name: str):
        self.steps.append(Step(kind="server", name=name))

    def add_impersonation(self, kind: str, value: str):
        self.steps.append(Step(kind="impersonate", imp_kind=kind, imp_value=value))

    def back(self) -> Optional[Step]:
        if self.steps:
            return self.steps.pop()
        return None

    # ---- derive frames from steps ----

    def frames(self) -> List[Frame]:
        frames: List[Frame] = [Frame(name=None)]
        for step in self.steps:
            if step.kind == "server":
                frames.append(Frame(name=step.name))
            else:  # impersonate -> applies to current (last) frame
                frames[-1].impersonate = (step.imp_kind, step.imp_value)
        return frames

    # ---- display ----

    def route_str(self) -> str:
        frames = self.frames()
        parts = []
        for frame in frames:
            if frame.name is None:
                label = c_local("local")
            else:
                label = c_hop(frame.name)
            if frame.impersonate:
                kind, value = frame.impersonate
                label += c_imp(f" [AS {kind}='{value}']")
            parts.append(label)
        return c_arrow(" -> ").join(parts)

    def depth(self) -> int:
        return len(self.frames()) - 1

    # ---- query building ----

    def build(self, sql: str) -> str:
        command = sql.strip().rstrip(";")
        frames = self.frames()

        # Walk from the deepest hop back to (but not including) local,
        # wrapping with EXEC('...') AT <server>, applying impersonation
        # for that frame to the command *before* it is wrapped so the
        # impersonation takes effect in that frame's own context.
        for frame in reversed(frames[1:]):
            if frame.impersonate:
                kind, value = frame.impersonate
                command = f"EXECUTE AS {kind} = '{sql_quote(value)}'; {command}"
            command = f"EXEC ('{sql_quote(command)}') AT {frame.name}"

        # Local frame impersonation happens before the first hop is issued.
        local = frames[0]
        if local.impersonate:
            kind, value = local.impersonate
            command = f"EXECUTE AS {kind} = '{sql_quote(value)}'; {command}"

        return command


# Canned enumeration queries, wrapped through the current route just like
# any other SQL. Useful for discovering what to do next at each hop.
STORED_QUERIES = {
    "list_servers": (
        "SELECT name, data_source, product, provider "
        "FROM sys.servers WHERE is_linked = 1"
    ),
    "list_impersonate_logins": (
        "SELECT DISTINCT b.name AS login_name "
        "FROM sys.server_permissions a "
        "INNER JOIN sys.server_principals b ON a.grantor_principal_id = b.principal_id "
        "WHERE a.permission_name = 'IMPERSONATE'"
    ),
    "list_impersonate_users": (
        "SELECT DISTINCT b.name AS user_name "
        "FROM sys.database_permissions a "
        "INNER JOIN sys.database_principals b ON a.grantor_principal_id = b.principal_id "
        "WHERE a.permission_name = 'IMPERSONATE'"
    ),
}


HELP_TEXT = """
Commands:
  linked_server <name>       Add a hop to <name>, context moves there
  impersonate_login <login>  EXECUTE AS LOGIN='<login>' at the current frame
  impersonate_user <user>    EXECUTE AS USER='<user>' at the current frame
  back                       Undo the last step issued (hop or impersonation)
  route                      Print the current route
  list_servers               Print query: linked servers visible from here
  list_impersonate_logins    Print query: server logins you can impersonate here
  list_impersonate_users     Print query: database users you can impersonate here
  help                       Show this help
  exit / quit                Leave

Anything else is treated as SQL and printed as the fully wrapped query, e.g.:

  SELECT USER_NAME()
  EXEC sp_helpdb 'master'
"""


def parse_command(line: str):
    parts = line.strip().split(None, 1)
    if not parts:
        return None, None
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    return cmd, arg


def print_route(state: "RouteState"):
    print(c_dim("route: ") + state.route_str())


def interactive():
    state = RouteState()

    print(Colors.wrap("Interactive linked-server route builder", Colors.BOLD))
    print_route(state)
    print(c_dim("type 'help' for commands"))
    print()

    while True:
        try:
            depth = state.depth()
            prompt = c_prompt(f"[{depth}]> ")
            line = input(prompt)
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print()
            continue

        if not line.strip():
            continue

        cmd, arg = parse_command(line)

        if cmd in {"exit", "quit", ".exit", ".quit"}:
            break

        if cmd in {"help", ".help"}:
            print(HELP_TEXT)
            continue

        if cmd in {"route", ".route"}:
            print(state.route_str())
            continue

        if cmd == "back":
            removed = state.back()
            if removed is None:
                print(c_err("nothing to undo"), file=sys.stderr)
            elif removed.kind == "server":
                print(c_dim("removed hop: ") + c_hop(removed.name))
            else:
                print(
                    c_dim("removed impersonation: ")
                    + c_imp(f"{removed.imp_kind}='{removed.imp_value}'")
                )
            print_route(state)
            continue

        if cmd == "linked_server":
            if not arg:
                print(c_err("usage: linked_server <name>"), file=sys.stderr)
                continue
            state.add_server(arg)
            print_route(state)
            continue

        if cmd == "impersonate_login":
            if not arg:
                print(c_err("usage: impersonate_login <login>"), file=sys.stderr)
                continue
            state.add_impersonation("LOGIN", arg)
            print_route(state)
            continue

        if cmd == "impersonate_user":
            if not arg:
                print(c_err("usage: impersonate_user <user>"), file=sys.stderr)
                continue
            state.add_impersonation("USER", arg)
            print_route(state)
            continue

        if cmd in STORED_QUERIES:
            print(c_ok("STORED query:"))
            print(c_query(state.build(STORED_QUERIES[cmd])))
            print()
            continue

        # otherwise: treat the whole line as SQL to wrap
        print(c_ok("wrapped query:"))
        print(c_query(state.build(line)))
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Interactive linked-server / impersonation query builder."
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color output (also respects NO_COLOR env var)",
    )
    args = parser.parse_args()

    Colors.configure(no_color_flag=args.no_color)
    interactive()


if __name__ == "__main__":
    main()