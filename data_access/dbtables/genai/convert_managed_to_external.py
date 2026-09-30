import logging

from common.utils.envsetup import get_base_parser, init_app_using_parser
from common.utils.sqlutil import execsql, readdfsql
from data_access.dbtables import GENAI_APP
from data_access.spark.api import sparkmanager

SCHEMA = "dbo"
BLOB_DIR = "tables/dbo"
SWAP_SUFFIX = "__ext_swap"


def location(table):
    return sparkmanager._getfilepath(table, BLOB_DIR).rstrip("/")


def _metadata(table):
    rows = readdfsql(
        "SELECT table_name, table_type, data_source_format, storage_path "
        "FROM information_schema.tables "
        f"WHERE table_schema = '{SCHEMA}' AND table_name = '{table}'",
        app=GENAI_APP,
    )
    if rows is None or rows.empty:
        return None
    row = rows.iloc[0]
    return {
        "table_type": str(row["table_type"]).upper(),
        "data_source_format": str(row["data_source_format"]).upper(),
        "storage_path": str(row["storage_path"]).rstrip("/"),
    }


def _row_count(qualified):
    return int(readdfsql(f"SELECT COUNT(*) AS n FROM {qualified}", app=GENAI_APP).iloc[0]["n"])


def _conforming(meta, table):
    return meta is not None and meta == {
        "table_type": "EXTERNAL",
        "data_source_format": "DELTA",
        "storage_path": location(table),
    }


def convert_table(table):
    qualified = f"{SCHEMA}.{table}"
    meta = _metadata(table)
    if meta is None:
        raise RuntimeError(f"{qualified} was not found")
    if _conforming(meta, table):
        logging.info("%s already external at %s", qualified, location(table))
        return
    target = location(table)
    swap = f"{table}{SWAP_SUFFIX}"
    swap_qualified = f"{SCHEMA}.{swap}"
    before = _row_count(qualified)
    execsql(f"DROP TABLE IF EXISTS {swap_qualified}", app=GENAI_APP)
    execsql(
        f"CREATE TABLE {swap_qualified} USING DELTA LOCATION '{target}' AS SELECT * FROM {qualified}",
        app=GENAI_APP,
    )
    copied = _row_count(swap_qualified)
    if copied != before:
        raise RuntimeError(f"{qualified} copy mismatch: expected {before}, got {copied}")
    execsql(f"DROP TABLE {qualified}", app=GENAI_APP)
    execsql(f"ALTER TABLE {swap_qualified} RENAME TO {table}", app=GENAI_APP)
    if not _conforming(_metadata(table), table):
        raise RuntimeError(f"{qualified} is still not an external Delta table at {target}")
    after = _row_count(qualified)
    if after != before:
        raise RuntimeError(f"{qualified} row count changed: {before} -> {after}")
    logging.info("%s converted to EXTERNAL DELTA at %s rows=%s", qualified, target, after)


def managed_genai_tables():
    rows = readdfsql(
        "SELECT table_name FROM information_schema.tables "
        f"WHERE table_schema = '{SCHEMA}' AND table_name LIKE 'genai_%' "
        "AND table_type != 'VIEW' AND UPPER(table_type) = 'MANAGED' "
        "ORDER BY table_name",
        app=GENAI_APP,
    )
    if rows is None or rows.empty:
        return []
    return list(rows["table_name"])


def convert_managed_genai_tables(tables=None):
    selected = tuple(tables or managed_genai_tables())
    for table in selected:
        convert_table(table)
    logging.info("converted %s managed genai table(s)", len(selected))


def get_parser():
    parser = get_base_parser("convert_managed_genai_tables")
    parser.add_argument("--table", default="all")
    return parser


if __name__ == "__main__":
    args = init_app_using_parser(get_parser())
    convert_managed_genai_tables(None if args.table == "all" else [args.table])
