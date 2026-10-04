# spwned-servers

Interactive query builder for chaining MSSQL **linked servers** and
**impersonation** (`EXECUTE AS LOGIN` / `EXECUTE AS USER`) into a single
nested query — without hand-writing nested `EXEC('...') AT server` strings
every time.

Build a route hop by hop, add impersonation at any point in the chain, and
get the fully wrapped query printed out, ready to paste into a query tool
or run remotely. Nothing is executed by this tool — it only builds and
prints SQL.

## Why

Pivoting through linked servers usually means writing something like:

```sql
EXEC ('EXECUTE AS LOGIN = ''sa''; EXEC (''SELECT USER_NAME()'') AT DB01') AT DB02
```

by hand, and re-deriving it every time the path, depth, or impersonation
changes. `spwned-servers` keeps the route as state so you can extend it,
back out of a bad hop, or just re-print the route at any depth.

## Requirements

- Python 3.9+
- No external dependencies

## Usage

```bash
python3 spwned_servers.py
```

This drops you into an interactive shell. The prompt shows your current
depth (`[0]>` = local, `[1]>` = one hop in, etc).

### Commands

| Command                       | Description                                                   |
|--------------------------------|-----------------------------------------------------------------|
| `linked_server <name>`         | Add a hop to `<name>`; context moves there                     |
| `impersonate_login <login>`    | `EXECUTE AS LOGIN='<login>'` at the current frame               |
| `impersonate_user <user>`      | `EXECUTE AS USER='<user>'` at the current frame                 |
| `back`                         | Undo the last step issued (hop or impersonation)                |
| `route`                        | Print the current route                                         |
| `list_servers`                 | Print query: linked servers visible from the current frame      |
| `list_impersonate_logins`      | Print query: server logins impersonable from here               |
| `list_impersonate_users`       | Print query: database users impersonable from here               |
| `help`                         | Show command help                                                |
| `exit` / `quit`                | Leave                                                            |

Anything else typed at the prompt is treated as a raw SQL statement and
printed as the fully wrapped query.

Impersonation always applies to the **current frame** — whichever hop you
most recently added with `linked_server`, or `local` if you haven't added
one yet. Issuing `impersonate_login`/`impersonate_user` again on the same
frame overwrites the previous impersonation for that frame.

### Example session

```
$ python3 spwned_servers.py
wrapper2 linked-server route builder
route: local
type 'help' for commands

[0]> linked_server DB02
route: local -> DB02
[1]> impersonate_login sa
route: local -> DB02 [AS LOGIN='sa']
[1]> linked_server DB01
route: local -> DB02 [AS LOGIN='sa'] -> DB01
[2]> SELECT USER_NAME()
EXEC ('EXECUTE AS LOGIN = ''sa''; EXEC (''SELECT USER_NAME()'') AT DB01') AT DB02

[2]> back
removed hop: DB01
route: local -> DB02 [AS LOGIN='sa']
[1]> list_impersonate_users
EXEC ('EXECUTE AS LOGIN = ''sa''; SELECT DISTINCT b.name AS user_name FROM sys.database_permissions a INNER JOIN sys.database_principals b ON a.grantor_principal_id = b.principal_id WHERE a.permission_name = ''IMPERSONATE''') AT DB02
```

## Notes

- Depth is unbounded — chain as many hops as the environment allows.
- No `REVERT` is inserted after an impersonated block; add it yourself in
  the SQL you type if a given statement needs it.
- This tool only **builds and prints** queries. You still need a way to
  actually run them (e.g. `sqlcmd`, `impacket-mssqlclient`, SSMS, etc.).

## Disclaimer

For use against systems you are authorized to test. You're responsible for
how you use it.
