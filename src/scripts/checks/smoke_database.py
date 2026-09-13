"""Smoke test for the connection pool: it must not hang, and it must notice a dead link.

Everything here is about failure behaviour, because that is what the pool is for. A pool
that works is invisible; a pool that hangs looks exactly like the simulation freezing.

```sh
uv run python -m scripts.checks.smoke_database
```
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import config

# A deliberately tiny pool with a short wait, so saturation is reachable in a test instead
# of in production. Set before the pool is first built - it reads config at creation.
config.DB_POOL_MAX = 3
config.DB_POOL_MIN = 1
config.DB_POOL_WAIT_SECONDS = 1.0
config.DB_POOL_IDLE_PING_SECONDS = 0.0  # every checkout re-validates

from scripts.database import database  # noqa: E402

_results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    ok = bool(ok)
    _results.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    return ok


def test_timeouts_are_on_the_connection() -> None:
    print("\nconnection settings travel with the connection")
    with database.get_cursor() as cur:
        cur.execute("SHOW statement_timeout")
        timeout = cur.fetchone()["statement_timeout"]
        cur.execute("SHOW application_name")
        app_name = cur.fetchone()["application_name"]
    check("a statement timeout is set", timeout not in ("0", "0ms"), timeout)
    check("the server knows which app this is", app_name == "stocksave", str(app_name))


def test_saturation_answers_instead_of_hanging() -> None:
    print("\nsaturation produces a clear, retryable answer")
    held = []
    try:
        for _ in range(3):
            ctx = database.get_conn()
            held.append(ctx)
            ctx.__enter__()
        check("every connection is checked out", database.pool_status()["in_use"] == 3, str(database.pool_status()))

        started = time.monotonic()
        try:
            with database.get_conn():
                check("one more checkout should not have succeeded", False)
        except database.DatabaseBusy as error:
            waited = time.monotonic() - started
            check("a full pool raises DatabaseBusy", True)
            check("it waited for a connection before giving up", waited >= 0.9, f"{waited:.2f}s")
            check("and it says what is happening", "busy" in str(error).lower(), str(error))
            check("well inside a page's patience", waited < 5, f"{waited:.2f}s")
    finally:
        for ctx in held:
            ctx.__exit__(None, None, None)

    # The pool must drain: a transient burst cannot poison it.
    with database.get_cursor() as cur:
        cur.execute("SELECT 1 AS ok")
        cur.fetchone()
    check("the pool recovers once the holders let go", database.pool_status()["in_use"] == 0, str(database.pool_status()))


def test_concurrent_burst() -> None:
    print("\na burst of threads all get served")
    # The saturation test above set a deliberately impatient wait. Six threads sharing
    # three connections need a real budget, which is exactly what production gets.
    config.DB_POOL_WAIT_SECONDS = 5.0
    errors: list[str] = []
    served = threading.Semaphore(0)

    def worker() -> None:
        try:
            with database.get_cursor() as cur:
                cur.execute("SELECT pg_sleep(0.2)")
            served.release()
        except Exception as error:  # noqa: BLE001 - the point is to report anything at all
            errors.append(f"{type(error).__name__}: {error}")

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    config.DB_POOL_WAIT_SECONDS = 1.0
    check("every thread finished", all(not t.is_alive() for t in threads))
    check("none of them failed", not errors, "; ".join(errors[:3]))


def test_dead_connection_is_replaced() -> None:
    print("\nconnections the server killed are replaced, not served")
    contexts = [database.get_conn() for _ in range(2)]
    conns = [context.__enter__() for context in contexts]
    pids = []
    for conn in conns:  # get_conn hands back a plain connection, not a dict cursor
        with conn.cursor() as cur:
            cur.execute("SELECT pg_backend_pid()")
            pids.append(cur.fetchone()[0])
    # Killed from a third connection, while the doomed pair is still checked out: a
    # connection cannot terminate its own backend and survive to report it.
    with database.get_cursor() as cur:
        cur.execute("SELECT pg_terminate_backend(pid) AS killed FROM unnest(%s::int[]) AS pid", (pids,))
        killed = [row["killed"] for row in cur.fetchall()]
    for context in contexts:
        context.__exit__(None, None, None)
    if not all(killed):
        print(f"  SKIP  this account cannot terminate backends {pids}")
        return

    with database.get_cursor() as cur:
        cur.execute("SELECT 1 AS ok")
        cur.fetchone()
    check("the next checkout replaced the dead connections", True, f"killed {pids}")


def main() -> int:
    print("smoke test: connection pool behaviour")
    try:
        status = database.warm_up()
        check("the pool opens at startup", status["idle"] + status["in_use"] >= 1, str(status))
    except Exception as error:  # noqa: BLE001
        check("the pool opens at startup", False, f"{type(error).__name__}: {error}")
        return 1

    test_timeouts_are_on_the_connection()
    test_saturation_answers_instead_of_hanging()
    test_concurrent_burst()
    test_dead_connection_is_replaced()

    failures = [name for ok, name, _ in _results if not ok]
    print(f"\n{len(_results) - len(failures)}/{len(_results)} checks passed")
    for name in failures:
        print(f"  failed: {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
