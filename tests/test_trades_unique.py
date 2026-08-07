"""test_trades_unique.py — `trades` must reject a duplicate (exchange, symbol, trade_id).

RED FIRST. Before the constraint existed this file failed on the schema the collector
actually writes: 10,248 duplicate rows across 27 partitions of the frozen slice (0.041%,
peak 1.0016% on 2026-08-02), and 758 more on 2026-08-06 — recurring, so systemic.

Why a constraint and not a read-time DISTINCT: a DISTINCT at read time asks every future
reader to remember, and a rule that lives in the reader's memory is the rot pattern
`scripts/doc_freshness.py` exists to stop. The writer is the one place that can enforce it once.

Why the insert path had to change with it, measured before the change:
  * `INSERT OR IGNORE` RAISES on a table with no constraint (Binder Error), so day files
    created before this change cannot use it — the collector keeps today and yesterday, so
    both schemas are live at once.
  * a PLAIN `executemany` INSERT on a constrained table loses the WHOLE BATCH on one
    duplicate: offered 3 rows with 1 duplicate, 1 row stored, and in the collector that
    path does `rows_dropped_error += len(buf)` and evicts the connection.
  So a bare constraint would have converted a harmless byte-identical duplicate into real
  data loss. The writer therefore uses `INSERT OR IGNORE` where the constraint exists and
  the plain form where it does not.
"""
from __future__ import annotations

import duckdb
import pytest

from btcquant import collector


def _fresh(tmp_path):
    con = duckdb.connect(str(tmp_path / "t.duckdb"))
    for ddl in collector._SCHEMA_DDL:
        con.execute(ddl)
    return con


ROW = ("binancef", "BTCUSDT", "3403202817", 1785413659551, 64990.8, 0.01, False)


def test_trades_has_a_unique_constraint_on_the_trade_key(tmp_path):
    """The assertion that was RED: the shipped schema had no constraint at all."""
    con = _fresh(tmp_path)
    con.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?)", list(ROW))
    with pytest.raises(Exception) as e:
        con.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?)", list(ROW))
    assert "onstraint" in str(e.value) or "uplicate" in str(e.value), str(e.value)


def test_the_writer_skips_the_duplicate_instead_of_losing_the_batch(tmp_path):
    """The half that matters operationally.

    A batch of three rows containing one duplicate must store the two good ones. Under a
    plain INSERT the same batch stores ONE row and the collector counts three as dropped.
    """
    con = _fresh(tmp_path)
    con.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?)", list(ROW))
    batch = [ROW,
             ("binancef", "BTCUSDT", "3403202818", 1785413659552, 64999.9, 0.02, True),
             ("binancef", "BTCUSDT", "3403202819", 1785413659553, 64990.8, 0.03, False)]
    con.executemany(collector._INSERT_SQL["trades"], batch)
    assert con.execute("SELECT count(*) FROM trades").fetchone()[0] == 3, (
        "the duplicate was not skipped, or a good row was lost with it")


def test_the_insert_sql_still_works_on_a_pre_constraint_table(tmp_path):
    """Both schemas are live at once — the store keeps today AND yesterday.

    A day file created before this change has no constraint. The writer must still be able
    to insert into it; a Binder Error here would stop the collector writing to yesterday.
    """
    con = duckdb.connect(str(tmp_path / "old.duckdb"))
    con.execute("CREATE TABLE trades (exchange VARCHAR, symbol VARCHAR, trade_id VARCHAR, "
                "ts_ms BIGINT, price DOUBLE, qty DOUBLE, aggressor_buy BOOLEAN)")
    sql = collector.insert_sql_for(con, "trades")
    con.executemany(sql, [ROW, ROW])
    assert con.execute("SELECT count(*) FROM trades").fetchone()[0] == 2, (
        "a pre-constraint table should still accept both rows — nothing enforces uniqueness there")
