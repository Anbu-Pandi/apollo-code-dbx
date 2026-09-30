"""Unit tests for factset_bdc_enriched pure-logic functions.

Tests cover: build_date_chunks, clean_key, clean_id_bb8, clean_cusip9,
_variant_map, _explode_key_variants_chunked, prepare_factset_df,
build_pivoted_index_df, match_factset_to_index, and the
run_multivariant_join_enrichment orchestrator signature via
crosswalk_multivariant_enrichment.
"""
import numpy as np
import pandas as pd
import pytest

from data_access.dbtables.bdc import factset_bdc_enriched as enriched
from data_access.dbtables.bdc import holdings
from data_access.dbtables.bdc.factset_bdc_enriched import (
    INDEX_IDENTIFIER_COLS,
    INDEX_KEY_COLS,
    INDEX_METRIC_COLS,
    FACTSET_KEY_COLS,
    build_date_chunks,
    build_pivoted_index_df,
    clean_cusip9,
    clean_id_bb8,
    clean_key,
    match_factset_to_index,
    prepare_factset_df,
    resolve_bootstrap_holdings_rows,
    roll_forward_index_constituent_stats,
    run_pipeline,
    _variant_map,
    _explode_key_variants_chunked,
)


class TestBootstrapHoldingsRows:
    def test_uses_full_snapshot_when_bootstrap_date_has_no_updates(self):
        snapshot = pd.DataFrame(
            {
                "factset_bdc_holding_id": ["holding-1"],
                "collection_date": [pd.NaT],
                "fair_value": [100.0],
            }
        )

        result = resolve_bootstrap_holdings_rows(
            holdings_updates_df=pd.DataFrame(),
            factset_snapshot_df=snapshot,
            knowledge_date="2026-06-20",
            bootstrap_holdings=True,
        )

        assert len(result) == 1
        assert result.loc[0, "holding_row_key"] == "holding-1"
        assert result.loc[0, "knowledge_date"] == pd.Timestamp("2026-06-20")
        assert result.loc[0, "snapshot_date"] == pd.Timestamp("2026-06-20")

    def test_preserves_update_rows_when_available(self):
        updates = pd.DataFrame({"holding_row_key": ["holding-1"]})

        result = resolve_bootstrap_holdings_rows(
            holdings_updates_df=updates,
            factset_snapshot_df=pd.DataFrame(),
            knowledge_date="2026-06-20",
            bootstrap_holdings=True,
        )

        assert result is updates


def test_bootstrap_result_uses_full_partition_overwrite(monkeypatch):
    calls = []

    class FakeConf:
        mode = "dynamic"

        def get(self, _key, default):
            return self.mode or default

        def set(self, _key, value):
            self.mode = value
            calls.append(("mode", value))

    class FakeSpark:
        conf = FakeConf()

    def fake_write(*_args, **kwargs):
        calls.append(("write_mode", FakeSpark.conf.mode))
        assert kwargs["overwrite"] is True

    monkeypatch.setattr(enriched.sparkmanager, "refresh_spark", lambda: None)
    monkeypatch.setattr(enriched.sparkmanager, "_spark", FakeSpark())
    monkeypatch.setattr(enriched.sparkmanager, "pandastodelta", fake_write)

    enriched._overwrite_bootstrap_result(
        pd.DataFrame({"knowledge_date": [pd.Timestamp("2026-06-17")]}),
        "result_table",
        "tables/dbo",
    )

    assert calls == [
        ("mode", "static"),
        ("write_mode", "static"),
        ("mode", "dynamic"),
    ]


class TestDatabricksRead:
    def test_uses_explicit_sql_connection_in_databricks_runtime(self, monkeypatch):
        expected = pd.DataFrame({"value": [1]})
        calls = {}

        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        def fake_fetch(sql, _conn, chunk_size):
            calls.update(sql=sql, chunk_size=chunk_size)
            return expected

        monkeypatch.setenv("DATABRICKS_RUNTIME_VERSION", "15.4")
        monkeypatch.setattr(
            enriched,
            "get_databricks_warehouse_connection",
            lambda app: calls.update(app=app) or FakeConnection(),
        )
        monkeypatch.setattr(enriched, "fetch_data_with_arrow", fake_fetch)

        actual = enriched._read_bdc_databricks_sql(
            "SELECT 1 AS value",
            msg="spark read test",
            databricks_app="databricks",
        )

        pd.testing.assert_frame_equal(actual, expected)
        assert calls == {
            "app": "databricks",
            "sql": "SELECT 1 AS value",
            "chunk_size": 10_000,
        }

    def test_holdings_reads_use_explicit_sql_connection_in_databricks_runtime(self, monkeypatch):
        expected = pd.DataFrame({"value": [1]})
        calls = {}

        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        def fake_connection(app):
            calls["app"] = app
            return FakeConnection()

        def fake_fetch(sql, _conn, chunk_size):
            calls.update(sql=sql, chunk_size=chunk_size)
            return expected

        monkeypatch.setenv("DATABRICKS_RUNTIME_VERSION", "15.4")
        monkeypatch.setattr(holdings, "get_databricks_warehouse_connection", fake_connection)
        monkeypatch.setattr(holdings, "fetch_data_with_arrow", fake_fetch)

        actual = holdings._read_databricks_sql("SELECT 1 AS value", msg="spark read test")

        pd.testing.assert_frame_equal(actual, expected)
        assert calls == {
            "sql": "SELECT 1 AS value",
            "app": "databricks",
            "chunk_size": 10_000,
        }

    def test_warehouse_connection_uses_configured_http_path(self, monkeypatch):
        values = {
            "databricks_host": "adb-dev.example.net",
            "databricks_http_path": "/sql/1.0/warehouses/dev",
            "databricks_pat": "token",
            "databricks_catalog": "apollo_market_data_dev",
            "schema": "dbo",
        }
        calls = {}
        monkeypatch.setattr(holdings.config, "getconfig", lambda _app, key: values.get(key))
        monkeypatch.setattr(
            holdings.databricks.sql,
            "connect",
            lambda **kwargs: calls.update(kwargs) or object(),
        )

        connection = holdings.get_databricks_warehouse_connection("databricks")

        assert connection is not None
        assert calls["http_path"] == "/sql/1.0/warehouses/dev"

    def test_statements_use_active_spark_in_databricks_runtime(self, monkeypatch):
        executed = []

        class FakeResult:
            def collect(self):
                executed.append("collected")

        class FakeSpark:
            def sql(self, sql):
                executed.append(sql)
                return FakeResult()

        monkeypatch.setenv("DATABRICKS_RUNTIME_VERSION", "15.4")
        monkeypatch.setattr(enriched.sparkmanager, "_spark", FakeSpark())
        monkeypatch.setattr(
            enriched.sparkmanager,
            "refresh_spark",
            lambda is_databricks_env=False: None,
        )
        monkeypatch.setattr(
            enriched,
            "execsql",
            lambda *_args, **_kwargs: pytest.fail("SQL connector must not be used in Databricks Runtime"),
        )

        enriched._execute_bdc_databricks_sql(
            "DELETE FROM dbo.target WHERE knowledge_date = DATE('2026-06-18')",
            msg="delete test slice",
            databricks_app="databricks",
        )

        assert executed == [
            "DELETE FROM dbo.target WHERE knowledge_date = DATE('2026-06-18')",
            "collected",
        ]


class TestFactSetKnowledgeUpdates:
    def test_marketplace_holdings_sql_uses_current_row_key_and_sec_fields(self):
        assert "CAST(h.record_id AS DOUBLE) AS factset_bdc_holding_id" in holdings.FACTSET_BDC_SQL
        assert "h.investment_name" in holdings.FACTSET_BDC_SQL
        assert "h.reported_industry" in holdings.FACTSET_BDC_SQL
        assert "h.bdc_id" not in holdings.FACTSET_BDC_SQL

    def test_marketplace_holdings_sql_prunes_reference_data_to_selected_reports(self):
        assert "selected_reports AS" in holdings.FACTSET_BDC_SQL
        assert "selected_holdings AS" in holdings.FACTSET_BDC_SQL
        assert "INNER JOIN lookup_fsym lf ON lf.fsym_id = dd.instrument_id" in holdings.FACTSET_BDC_SQL
        assert "FROM selected_holdings h" in holdings.FACTSET_BDC_SQL

    def test_skips_full_holdings_query_when_report_metadata_has_no_updates(self, monkeypatch):
        calls = []

        def fake_read(sql, **_kwargs):
            calls.append(sql)
            return pd.DataFrame({"report_id": []})

        monkeypatch.setattr(holdings, "_read_databricks_sql", fake_read)

        actual = holdings.get_factset_bdc_holdings_knowledge_updates(
            "2026-06-18",
            "2026-06-18",
            asof_date="2026-06-18",
        )

        assert actual.empty
        assert len(calls) == 1
        assert ".bdc_v1.bdc_reports" in calls[0]

    def test_loads_full_holdings_only_for_updated_reports(self, monkeypatch):
        calls = []

        def fake_read(sql, **_kwargs):
            calls.append(sql)
            if len(calls) == 1:
                return pd.DataFrame({"report_id": [101, 202]})
            return pd.DataFrame({"report_id": [101]})

        monkeypatch.setattr(holdings, "_read_databricks_sql", fake_read)

        actual = holdings.get_factset_bdc_holdings_knowledge_updates(
            "2026-06-18",
            "2026-06-18",
        )

        assert len(actual) == 1
        assert len(calls) == 2
        assert "r.report_id IN (101, 202)" in calls[1]

    def test_asof_query_can_limit_refresh_to_numpy_bdc_ids(self, monkeypatch):
        calls = []

        def fake_read(sql, **_kwargs):
            calls.append(sql)
            return pd.DataFrame({"factset_bdc_holding_id": [1.0]})

        monkeypatch.setattr(holdings, "_read_databricks_sql", fake_read)

        actual = holdings.get_factset_bdc_holdings_asof(
            "2026-06-18",
            factset_entity_ids=np.array(["BDC2", "O'NEIL", "BDC1"]),
        )

        assert len(actual) == 1
        assert "factset_entity_id IN ('BDC1', 'BDC2', 'O''NEIL')" in calls[0]


class TestIndexSourceRead:
    def test_daily_read_keeps_only_latest_date_per_source(self, monkeypatch):
        calls = []

        def fake_read(sql, **_kwargs):
            calls.append(sql)
            return pd.DataFrame(
                {
                    "asof_date": ["2026-06-18"],
                    "source": ["JPM"],
                }
            )

        monkeypatch.setattr(enriched, "_read_bdc_databricks_sql", fake_read)

        actual = enriched.fetch_index_constituent_stats_chunked(
            index_view="dbo.v_index_constituent_stats",
            from_date="2026-06-15",
            to_date="2026-06-18",
            enable_parallel=False,
            latest_per_source=True,
        )

        assert len(actual) == 1
        assert len(calls) == 1
        assert "MAX(CAST(asof_date AS DATE)) OVER (PARTITION BY source)" in calls[0]
        assert "SELECT *" not in calls[0]

    def test_direct_bridge_prefers_first_mapped_key_then_first_available(self):
        index_df = pd.DataFrame(
            {
                "source": ["JPM", "JPM"],
                "_bridge_key_1": ["UNMAPPED", "RAW_BBG"],
                "_bridge_key_2": ["MAPPED_LOANX", pd.NA],
                "_bridge_key_3": ["CUSIP1", "CUSIP2"],
                "_bridge_key_4": [pd.NA, pd.NA],
            }
        )

        actual = enriched.enrich_direct_index_bbg(
            index_df,
            bbg_bridge={"MAPPED_LOANX": "BBG000001234"},
        )

        assert actual.loc[0, "linked_instrument_id_bb"] == "MAPPED_LOANX"
        assert actual.loc[0, "bbg_match_source_key_type"] == "loanx_id"
        assert actual.loc[0, "instrument_id_bb"] == "BBG000001234"
        assert actual.loc[0, "id_bb8"] == "BBG00000"
        assert actual.loc[1, "linked_instrument_id_bb"] == "RAW_BBG"
        assert actual.loc[1, "bbg_match_source_key_type"] == "bbg_id"
        assert pd.isna(actual.loc[1, "instrument_id_bb"])
        assert not any(column.startswith("_bridge_key_") for column in actual.columns)

    def test_direct_read_normalizes_dates(self, monkeypatch):
        captured = {}

        def fake_direct(from_date, to_date):
            captured["dates"] = (from_date, to_date)
            return pd.DataFrame(
                {
                    "asof_date": ["2026-06-18"],
                    "source": ["ICE"],
                    "_bridge_key_1": ["US123"],
                    "_bridge_key_2": [pd.NA],
                    "_bridge_key_3": [pd.NA],
                    "_bridge_key_4": [pd.NA],
                }
            )

        monkeypatch.setattr(enriched, "get_direct_index_constituent_stats", fake_direct)
        actual = enriched.fetch_index_constituent_stats_direct(
            "2026-06-15",
            "2026-06-18",
            bbg_bridge={"US123": "BBG000001234"},
        )

        assert captured["dates"] == ("2026-06-15", "2026-06-18")
        assert actual.loc[0, "asof_date"] == pd.Timestamp("2026-06-18")
        assert actual.loc[0, "instrument_id_bb"] == "BBG000001234"

    def test_direct_read_applies_relevant_ice_bond_map(self, monkeypatch):
        monkeypatch.setattr(
            enriched,
            "get_direct_index_constituent_stats",
            lambda *_args: pd.DataFrame(
                {
                    "asof_date": ["2026-06-18"],
                    "source": ["ICE"],
                    "isin": ["US123"],
                    "_bridge_key_1": ["US123"],
                    "_bridge_key_2": [pd.NA],
                    "_bridge_key_3": [pd.NA],
                    "_bridge_key_4": [pd.NA],
                }
            ),
        )
        monkeypatch.setattr(
            enriched,
            "get_direct_index_bond_security_map",
            lambda *_args: {"US123": "BBG000001234"},
        )

        actual = enriched.fetch_index_constituent_stats_direct(
            "2026-06-15",
            "2026-06-18",
            bbg_bridge={},
            factset_bbg_ids={"BBG000001234"},
        )

        assert actual.loc[0, "instrument_id_bb"] == "BBG000001234"
        assert actual.loc[0, "id_bb8"] == "BBG00000"

    def test_collect_factset_bbg_ids_uses_mapped_and_raw_columns(self):
        actual = enriched.collect_factset_bbg_ids(
            pd.DataFrame(
                {
                    "instrument_id_bb": [" bbg000001234 ", "COEH3905286"],
                    "factset_sym_bbg_bbg_id": [pd.NA, "raw456"],
                    "unrelated": ["ignore", "ignore"],
                }
            )
        )

        assert actual == {"BBG000001234", "COEH3905286", "EH3905286", "RAW456"}


class TestPreviousSnapshotReuse:
    def test_previous_snapshot_removes_daily_enrichment_columns(self, monkeypatch):
        previous = pd.DataFrame(
            {
                "knowledge_date": ["2026-06-17"],
                "report_date": ["2026-03-31"],
                "factset_bdc_holding_id": [101.0],
                "bdc_factset_entity_id": ["BDC1"],
                "portfolio_name_factset": ["Issuer"],
                "index_price": [99.5],
                "JPM_price": [99.5],
                "position_row_id": [1],
                "holdings_snapshot_version": [enriched.HOLDINGS_SNAPSHOT_VERSION],
            }
        )
        monkeypatch.setattr(enriched, "_read_bdc_databricks_sql", lambda *_args, **_kwargs: previous)

        actual = enriched.fetch_previous_enriched_factset_snapshot("2026-06-18")

        assert list(actual.columns) == [
            "report_date",
            "factset_bdc_holding_id",
            "bdc_factset_entity_id",
            "portfolio_name_factset",
        ]

    def test_previous_snapshot_rejects_pre_versioned_slice(self, monkeypatch):
        previous = pd.DataFrame(
            {
                "knowledge_date": ["2026-06-17"],
                "factset_bdc_holding_id": [101.0],
            }
        )
        monkeypatch.setattr(enriched, "_read_bdc_databricks_sql", lambda *_args, **_kwargs: previous)

        assert enriched.fetch_previous_enriched_factset_snapshot("2026-06-18").empty

    def test_catchup_window_starts_after_previous_knowledge_date(self):
        previous = pd.DataFrame({"factset_bdc_holding_id": [1.0]})
        previous.attrs["knowledge_date"] = pd.Timestamp("2026-06-15")

        assert enriched.resolve_holdings_update_from("2026-06-18", previous) == "2026-06-16"

    def test_refreshed_bdc_replaces_only_its_previous_rows(self):
        previous = pd.DataFrame(
            {
                "bdc_factset_entity_id": ["BDC1", "BDC2"],
                "factset_bdc_holding_id": [1.0, 2.0],
            }
        )
        refreshed = pd.DataFrame(
            {
                "bdc_factset_entity_id": ["BDC1", "BDC1"],
                "factset_bdc_holding_id": [3.0, 4.0],
            }
        )

        actual = enriched.merge_refreshed_bdc_holdings(previous, refreshed)

        assert actual["factset_bdc_holding_id"].tolist() == [2.0, 3.0, 4.0]

    def test_empty_refresh_removes_affected_bdc(self):
        previous = pd.DataFrame(
            {
                "bdc_factset_entity_id": ["BDC1", "BDC2"],
                "factset_bdc_holding_id": [1.0, 2.0],
            }
        )

        actual = enriched.merge_refreshed_bdc_holdings(
            previous_snapshot=previous,
            refreshed_snapshot=pd.DataFrame(),
            refreshed_bdc_ids=["BDC1"],
        )

        assert actual["factset_bdc_holding_id"].tolist() == [2.0]


# ---------------------------------------------------------------------------
# build_date_chunks
# ---------------------------------------------------------------------------
class TestBuildDateChunks:
    def test_single_chunk(self):
        chunks = build_date_chunks("2025-01-01", "2025-02-15", chunk_months=3)
        assert len(chunks) == 1
        assert chunks[0] == ("2025-01-01", "2025-02-15")

    def test_multiple_chunks(self):
        chunks = build_date_chunks("2025-01-01", "2025-09-30", chunk_months=3)
        assert len(chunks) >= 3
        assert chunks[0][0] == "2025-01-01"
        assert chunks[-1][1] == "2025-09-30"
        # Chunks should be contiguous (no gaps)
        for i in range(1, len(chunks)):
            prev_end = pd.to_datetime(chunks[i - 1][1])
            cur_start = pd.to_datetime(chunks[i][0])
            assert (cur_start - prev_end).days == 1

    def test_same_day(self):
        chunks = build_date_chunks("2025-06-15", "2025-06-15", chunk_months=1)
        assert len(chunks) == 1
        assert chunks[0] == ("2025-06-15", "2025-06-15")


# ---------------------------------------------------------------------------
# clean_key / clean_id_bb8 / clean_cusip9
# ---------------------------------------------------------------------------
class TestCleanHelpers:
    def test_clean_key_basic(self):
        s = pd.Series(["  abc  ", "NaN", "", None, "GOOD"])
        out = clean_key(s)
        assert out.iloc[0] == "ABC"
        assert pd.isna(out.iloc[1])
        assert pd.isna(out.iloc[2])
        assert pd.isna(out.iloc[3])
        assert out.iloc[4] == "GOOD"

    def test_clean_id_bb8(self):
        s = pd.Series(["BBG12345CORP", "SHORT", None])
        out = clean_id_bb8(s)
        assert out.iloc[0] == "BBG12345"
        assert pd.isna(out.iloc[1])
        assert pd.isna(out.iloc[2])

    def test_clean_cusip9(self):
        s = pd.Series(["123456789", "12345", "ABCDE-FGH-I", None])
        out = clean_cusip9(s)
        assert out.iloc[0] == "123456789"
        assert pd.isna(out.iloc[1])
        assert out.iloc[2] == "ABCDEFGHI"


# ---------------------------------------------------------------------------
# _variant_map
# ---------------------------------------------------------------------------
class TestVariantMap:
    def test_raw_variant(self):
        s = pd.Series(["BBG12345", " abc ", None])
        variants = _variant_map(s, key_col_name="instrument_id_bb")
        assert "raw" in variants
        assert variants["raw"].iloc[0] == "BBG12345"
        assert variants["raw"].iloc[1] == "ABC"
        assert pd.isna(variants["raw"].iloc[2])

    def test_co_prefix_strip_on_bbg_col(self):
        s = pd.Series(["COEH3905286", "BBG12345", "NOTCO"])
        variants = _variant_map(s, key_col_name="instrument_id_bb")
        assert "co_prefix_strip2" in variants
        assert variants["co_prefix_strip2"].iloc[0] == "EH3905286"
        # BBG12345 doesn't start with CO after alnum cleanup -> should be NA
        # (BBG12345 -> BBG12345 -> doesn't start with CO)
        # Wait — actually it does NOT start with CO

    def test_co_prefix_strip_not_on_non_bbg_col(self):
        s = pd.Series(["COEH3905286"])
        variants = _variant_map(s, key_col_name="cusip")
        # co_prefix_strip2 should be NA for non-bbg columns
        assert pd.isna(variants["co_prefix_strip2"].iloc[0])

    def test_isin_to_cusip9(self):
        s = pd.Series(["US1234567890"])
        variants = _variant_map(s, key_col_name="isin")
        assert variants["isin_to_cusip9"].iloc[0] == "123456789"


# ---------------------------------------------------------------------------
# _explode_key_variants_chunked
# ---------------------------------------------------------------------------
class TestExplodeKeyVariantsChunked:
    def test_basic_explode(self):
        df = pd.DataFrame({
            "row_id": [1, 2],
            "key_a": ["BBG001", "BBG002"],
            "key_b": ["US123456789X", pd.NA],
        })
        result = _explode_key_variants_chunked(
            df,
            row_id_cols=["row_id"],
            key_cols=["key_a", "key_b"],
            key_col_name="fk_col",
            key_priority_map={"key_a": 0, "key_b": 1},
            variant_col_name="fk_variant",
            allowed_values=None,
            chunk_size=100,
            max_parallel_workers=1,
        )
        assert not result.empty
        assert "row_id" in result.columns
        assert "fk_col" in result.columns
        assert "fk_variant" in result.columns
        assert "key_value" in result.columns
        # row_id=1 should appear for both key_a and key_b
        row1 = result[result["row_id"] == 1]
        assert set(row1["fk_col"].unique()) >= {"key_a"}

    def test_allowed_values_filter(self):
        df = pd.DataFrame({
            "row_id": [1],
            "key_a": ["BBG001"],
        })
        result = _explode_key_variants_chunked(
            df,
            row_id_cols=["row_id"],
            key_cols=["key_a"],
            key_col_name="fk_col",
            key_priority_map={"key_a": 0},
            variant_col_name="fk_variant",
            allowed_values={"NOMATCH"},
            chunk_size=100,
            max_parallel_workers=1,
        )
        assert result.empty

    def test_empty_df(self):
        df = pd.DataFrame(columns=["row_id", "key_a"])
        result = _explode_key_variants_chunked(
            df,
            row_id_cols=["row_id"],
            key_cols=["key_a"],
            key_col_name="fk_col",
            key_priority_map={"key_a": 0},
            variant_col_name="fk_variant",
        )
        assert result.empty


# ---------------------------------------------------------------------------
# prepare_factset_df  (mocked bbg_bridge)
# ---------------------------------------------------------------------------
class TestPrepareFactsetDf:
    @staticmethod
    def _make_factset_df(n_entities=2, n_reports=2):
        rows = []
        rid = 1
        for eid in range(1, n_entities + 1):
            for rpt in range(n_reports):
                dt = pd.Timestamp("2025-06-30") + pd.DateOffset(months=rpt * 3)
                rows.append({
                    "report_date": dt,
                    "publication_date": dt + pd.Timedelta(days=30),
                    "period_type_code": 1,
                    "reporting_period_desc": "Quarterly",
                    "report_currency": "USD",
                    "sec_accession_id": f"ACC{rid}",
                    "bdc_factset_entity_id": f"ENT{eid}",
                    "bdc_name_factset": f"BDC{eid}",
                    "factset_report_id": rid,
                    "factset_portfolio_entity_id": f"PORT{eid}",
                    "portfolio_name_factset": f"Portfolio{eid}",
                    "fsym_id": f"FSYM{rid}",
                    "dcs_lookup_fsym_id": f"FSYM{rid}",
                    "factset_sym_cusip_loan_cusip": None,
                    "factset_sym_cusip_loan_ppn_cusip": None,
                    "factset_sym_cusip_loan_pcl_cusip": None,
                    "factset_sym_cusip_hist_cusip": None,
                    "factset_sym_isin_hist_isin": None,
                    "factset_sym_bbg_bbg_id": f"BBG{rid:06d}",
                    "bdc_security_desc": f"Security {rid}",
                    "investment_group": "Debt",
                    "investment_desc": "First Lien",
                    "debt_code_description": "Term Loan",
                    "portfolio_industry_code": None,
                    "portfolio_industry_desc": "Technology",
                    "portfolio_sector_code": None,
                    "seniority_desc": "Senior",
                    "coupon_desc": "Float",
                    "coupon_index": "SOFR",
                    "min_coupon_rate": 0.05,
                    "min_coupon_margin": 0.03,
                    "pay_in_kind_coupon_rate": None,
                    "instrument_currency": "USD",
                    "issue_date": dt - pd.Timedelta(days=365),
                    "acquisition_date": dt - pd.Timedelta(days=300),
                    "maturity_date": dt + pd.Timedelta(days=1825),
                    "amount_issued": 1e8,
                    "par_value": 1e6,
                    "fair_value": 9.5e5,
                    "cost": 1e6,
                    "net_assets_pct": 0.02,
                    "fair_to_par_value_ratio": 95.0,
                    "convert_par_value_flag": 0,
                    "exchange_rate": 1.0,
                    "non_accrual_status": 0,
                    "defaulted_flag": 0,
                    "perpetual_flag": 0,
                    "convertible_flag": 0,
                    "pay_in_kind_flag": 0,
                    "terminated_flag": 0,
                    "dcs_detail_id": rid,
                    "dcs_report_date": dt,
                    "dcs_instrument_description": f"DCS {rid}",
                    "dcs_debt_code": "TL",
                    "dcs_debt_desc": "Term Loan",
                    "dcs_issue_type_code": "D",
                    "dcs_issue_type_desc": "Debt",
                    "dcs_seniority_code": "S",
                    "dcs_seniority_desc": "Senior",
                    "dcs_coupon_code": "F",
                    "dcs_coupon_index": "SOFR",
                    "dcs_coupon_rate_min": 0.05,
                    "dcs_coupon_margin_min": 0.03,
                    "dcs_issue_date": dt - pd.Timedelta(days=365),
                    "dcs_maturity_date": dt + pd.Timedelta(days=1825),
                    "dcs_issuance_currency": "USD",
                    "dcs_currency": "USD",
                    "dcs_issue_amount": 1e8,
                    "dcs_os_amount": 9e7,
                    "dcs_avail_amount": 0,
                    "dcs_borrowing_base": 0,
                    "dcs_isin": None,
                    "dcs_bbg_id": f"BBG{rid:06d}",
                    "dcs_bbg_ticker": f"TICK{rid}",
                    "dcs_joined_fsym_id": f"FSYM{rid}",
                    "dcs_join_source": "fsym_id_security_map",
                })
                rid += 1
        return pd.DataFrame(rows)

    def test_prepare_adds_position_row_id(self):
        df = self._make_factset_df()
        bbg_bridge = {f"BBG{i:06d}": f"MAPPED{i:06d}" for i in range(1, 10)}
        result = prepare_factset_df(df, bbg_bridge=bbg_bridge)
        assert "position_row_id" in result.columns
        assert "next_report_date" in result.columns
        assert len(result) == len(df)
        # position_row_id should be sequential 1..N
        assert result["position_row_id"].iloc[0] == 1
        assert result["position_row_id"].iloc[-1] == len(result)

    def test_prepare_empty_raises(self):
        with pytest.raises(RuntimeError):
            prepare_factset_df(pd.DataFrame(), bbg_bridge={})


# ---------------------------------------------------------------------------
# build_pivoted_index_df
# ---------------------------------------------------------------------------
class TestBuildPivotedIndexDf:
    @staticmethod
    def _make_index_df(n=5):
        rows = []
        for i in range(n):
            row = {
                "asof_date": pd.Timestamp("2025-09-30") + pd.Timedelta(days=i),
                "source": "SP",
            }
            for col in INDEX_IDENTIFIER_COLS:
                if col == "asof_date":
                    continue
                row[col] = f"VAL_{col}_{i}"
            for col in INDEX_METRIC_COLS:
                row[col] = float(i)
            rows.append(row)
        return pd.DataFrame(rows)

    def test_basic_pivot(self):
        raw = self._make_index_df()
        index_df, pivot_cols = build_pivoted_index_df(raw)
        assert not index_df.empty
        assert "index_asof_date" in index_df.columns
        assert "index_row_id" in index_df.columns
        assert len(pivot_cols) > 0
        # Each pivot col should follow pattern SOURCE_METRIC
        for pc in pivot_cols:
            assert pc.startswith("SP_")

    def test_empty_raises(self):
        with pytest.raises(RuntimeError):
            build_pivoted_index_df(pd.DataFrame())

    def test_missing_cols_raises(self):
        df = pd.DataFrame({"asof_date": [pd.Timestamp("2025-01-01")], "source": ["SP"]})
        with pytest.raises(RuntimeError):
            build_pivoted_index_df(df)

    def test_source_asof_date_pivots_as_source_metric(self):
        raw = self._make_index_df()
        raw = pd.concat([raw, raw.copy()], ignore_index=True)
        raw.loc[0, "source"] = "JPM"
        raw.loc[0, "price"] = 99.0
        raw.loc[0, "source_asof_date"] = pd.Timestamp("2026-04-02")
        raw.loc[1, "source"] = "SP"
        raw.loc[1, "price"] = 101.0
        raw.loc[1, "source_asof_date"] = pd.Timestamp("2026-04-03")
        raw = raw.iloc[:2].copy()
        for col in INDEX_IDENTIFIER_COLS:
            raw.loc[:, col] = raw.iloc[0][col]

        index_df, pivot_cols = build_pivoted_index_df(raw)

        assert len(index_df) == 1
        assert index_df.iloc[0]["index_source"] == "JPM,SP"
        assert index_df.iloc[0]["JPM_price"] == 99.0
        assert index_df.iloc[0]["SP_price"] == 101.0
        assert index_df.iloc[0]["JPM_source_asof_date"] == pd.Timestamp("2026-04-02")
        assert index_df.iloc[0]["SP_source_asof_date"] == pd.Timestamp("2026-04-03")
        assert "JPM_source_asof_date" in pivot_cols


class TestRollForwardIndexConstituentStats:
    def test_rolls_each_source_to_latest_available_date(self):
        raw = pd.DataFrame(
            {
                "asof_date": pd.to_datetime(
                    ["2026-04-02", "2026-04-03", "2026-04-02", "2026-04-06"]
                ),
                "source": ["JPM", "SP", "ICE", "JPM"],
                "constituent_id": ["JPM_1", "SP_1", "ICE_1", "JPM_FUTURE"],
                "price": [99.0, 101.0, 98.0, 100.0],
            }
        )

        rolled = roll_forward_index_constituent_stats(raw, target_date="2026-04-03")

        assert set(rolled["source"]) == {"JPM", "SP", "ICE"}
        assert set(rolled["asof_date"]) == {pd.Timestamp("2026-04-03")}
        latest = dict(zip(rolled["source"], rolled["source_asof_date"]))
        assert latest["JPM"] == pd.Timestamp("2026-04-02")
        assert latest["SP"] == pd.Timestamp("2026-04-03")
        assert latest["ICE"] == pd.Timestamp("2026-04-02")
        assert "JPM_FUTURE" not in set(rolled["constituent_id"])

    def test_roll_forward_accepts_utc_source_dates(self):
        raw = pd.DataFrame(
            {
                "asof_date": pd.to_datetime(
                    ["2025-09-30T00:00:00Z", "2025-10-01T00:00:00Z"]
                ),
                "source": ["ICE", "JPM"],
                "constituent_id": ["ICE_1", "JPM_1"],
                "price": [98.0, 99.0],
            }
        )

        rolled = roll_forward_index_constituent_stats(raw, target_date="2025-10-01")

        assert set(rolled["asof_date"]) == {pd.Timestamp("2025-10-01")}
        assert dict(zip(rolled["source"], rolled["source_asof_date"])) == {
            "ICE": pd.Timestamp("2025-09-30"),
            "JPM": pd.Timestamp("2025-10-01"),
        }


# ---------------------------------------------------------------------------
# match_factset_to_index (integration of key explosion + join)
# ---------------------------------------------------------------------------
class TestMatchFactsetToIndex:
    def test_basic_match(self):
        """Two positions share a key with two index rows -> should match."""
        factset_df = pd.DataFrame({
            "position_row_id": [1, 2],
            "report_date": pd.to_datetime(["2025-09-30", "2025-09-30"]),
            "next_report_date": pd.to_datetime([pd.NaT, pd.NaT]),
            "bdc_factset_entity_id": ["ENT1", "ENT1"],
            "id_bb8": ["BBG00001", "BBG00002"],
            "instrument_id_bb": ["BBG000011234", "BBG000021234"],
        })
        # Add remaining factset key cols as NA so they exist
        for c in FACTSET_KEY_COLS:
            if c not in factset_df.columns:
                factset_df[c] = pd.NA

        index_df = pd.DataFrame({
            "index_row_id": [1, 2],
            "index_asof_date": pd.to_datetime(["2025-10-01", "2025-10-01"]),
            "index_index_name": ["SP", "SP"],
            "index_instrument_id_bb": ["BBG000011234", "BBG000021234"],
            "index_id_bb8": ["BBG00001", "BBG00002"],
            "index_source": ["SP", "SP"],
        })
        # Add remaining index key cols as NA
        for c in INDEX_KEY_COLS:
            if c not in index_df.columns:
                index_df[c] = pd.NA

        pivot_cols = ["SP_price", "SP_spread"]
        for pc in pivot_cols:
            index_df[pc] = 100.0

        result, join_summary, matches_ts, best_day = match_factset_to_index(
            factset_df, index_df, pivot_cols, max_parallel_key_workers=1
        )
        assert not result.empty
        assert not matches_ts.empty
        assert not best_day.empty
        assert "matched_pairs" in join_summary.columns

    def test_matches_dcs_isin_to_index_cusip_variant(self):
        """DCS ISIN should produce a CUSIP9 variant and match loan-style index keys."""
        factset_df = pd.DataFrame({
            "position_row_id": [1],
            "report_date": pd.to_datetime(["2025-09-30"]),
            "next_report_date": pd.to_datetime([pd.NaT]),
            "bdc_factset_entity_id": ["ENT1"],
            "dcs_isin": ["US1234567890"],
        })
        for c in FACTSET_KEY_COLS:
            if c not in factset_df.columns:
                factset_df[c] = pd.NA

        index_df = pd.DataFrame({
            "index_row_id": [1],
            "index_asof_date": pd.to_datetime(["2025-10-01"]),
            "index_index_name": ["JPM_LOAN"],
            "index_cusip": ["123456789"],
            "index_source": ["JPM"],
            "JPM_price": [99.5],
        })
        for c in INDEX_KEY_COLS:
            if c not in index_df.columns:
                index_df[c] = pd.NA

        result, join_summary, matches_ts, best_day = match_factset_to_index(
            factset_df, index_df, ["JPM_price"], max_parallel_key_workers=1
        )

        assert len(result) == 1
        assert best_day.iloc[0]["factset_join_key_col"] == "dcs_isin"
        assert best_day.iloc[0]["factset_variant"] == "isin_to_cusip9"
        assert best_day.iloc[0]["index_join_key_col"] == "index_cusip"
        assert join_summary.iloc[0]["matched_pairs"] == 1
        assert not matches_ts.empty

    def test_matches_low_priority_factset_and_index_fallback_ids(self):
        """Legacy fallback IDs should remain available, but behind stronger keys."""
        factset_df = pd.DataFrame({
            "position_row_id": [1],
            "report_date": pd.to_datetime(["2025-09-30"]),
            "next_report_date": pd.to_datetime([pd.NaT]),
            "bdc_factset_entity_id": ["ENT1"],
            "dcs_detail_id": ["FAC-123"],
        })
        for c in FACTSET_KEY_COLS:
            if c not in factset_df.columns:
                factset_df[c] = pd.NA

        index_df = pd.DataFrame({
            "index_row_id": [1],
            "index_asof_date": pd.to_datetime(["2025-10-01"]),
            "index_index_name": ["JPM_LOAN"],
            "index_constituent_id": ["FAC-123"],
            "index_source": ["JPM"],
            "JPM_price": [100.25],
        })
        for c in INDEX_KEY_COLS:
            if c not in index_df.columns:
                index_df[c] = pd.NA

        result, _, _, best_day = match_factset_to_index(
            factset_df, index_df, ["JPM_price"], max_parallel_key_workers=1
        )

        assert len(result) == 1
        assert best_day.iloc[0]["factset_join_key_col"] == "dcs_detail_id"
        assert best_day.iloc[0]["index_join_key_col"] == "index_constituent_id"
        assert result.iloc[0]["JPM_price"] == 100.25

    def test_waterfall_continues_lower_keys_for_unmatched_sources(self):
        """A high-priority source hit should not block lower-priority keys for other sources."""
        factset_df = pd.DataFrame({
            "position_row_id": [1],
            "report_date": pd.to_datetime(["2025-09-30"]),
            "next_report_date": pd.to_datetime([pd.NaT]),
            "bdc_factset_entity_id": ["ENT1"],
            "instrument_id_bb": ["BBG000011234"],
            "dcs_isin": ["US1234567890"],
        })
        for c in FACTSET_KEY_COLS:
            if c not in factset_df.columns:
                factset_df[c] = pd.NA

        index_df = pd.DataFrame({
            "index_row_id": [1, 2],
            "index_asof_date": pd.to_datetime(["2025-10-01", "2025-10-01"]),
            "index_index_name": ["SP_LOAN_INDEX", "JPM_LOAN"],
            "index_instrument_id_bb": ["BBG000011234", pd.NA],
            "index_cusip": [pd.NA, "123456789"],
            "index_source": ["SP", "JPM"],
            "SP_price": [100.0, pd.NA],
            "JPM_price": [pd.NA, 99.5],
        })
        for c in INDEX_KEY_COLS:
            if c not in index_df.columns:
                index_df[c] = pd.NA

        result, _, matches_ts, _ = match_factset_to_index(
            factset_df,
            index_df,
            ["SP_price", "JPM_price"],
            max_parallel_key_workers=1,
            waterfall=True,
        )

        assert result.iloc[0]["SP_price"] == 100.0
        assert result.iloc[0]["JPM_price"] == 99.5
        assert set(matches_ts["index_source"]) == {"SP", "JPM"}
        assert {"instrument_id_bb", "dcs_isin"}.issubset(set(matches_ts["factset_join_key_col"]))


class TestDailyRunPipeline:
    def test_daily_mode_uses_single_asof_index_window(self, monkeypatch):
        captured = {}

        def fake_get_factset_bdc_holdings_asof(asof_date):
            captured["factset_asof"] = asof_date
            return pd.DataFrame({"raw": [1]})

        def fake_prepare_factset_df(_df, bbg_bridge=None):
            assert bbg_bridge == {}
            return pd.DataFrame(
                {
                    "position_row_id": [1],
                    "report_date": pd.to_datetime(["2015-06-30"]),
                    "asof_date": pd.to_datetime(["2026-03-23"]),
                    "bdc_factset_entity_id": ["ENT1"],
                    "factset_report_id": [1],
                    "fsym_id": ["FSYM1"],
                }
            )

        def fake_fetch_factset_holdings_knowledge(**kwargs):
            captured["holdings_knowledge_kwargs"] = kwargs
            return pd.DataFrame(
                {
                    "holding_row_key": ["H1"],
                    "holding_row_hash": ["HASH1"],
                    "report_date": pd.to_datetime(["2015-06-30"]),
                }
            )

        def fake_select_incremental_holdings_rows(**kwargs):
            captured["incremental_input_rows"] = len(kwargs["holdings_snapshot_df"])
            return kwargs["holdings_snapshot_df"].copy()

        def fake_fetch_index_constituent_stats_direct(**kwargs):
            captured["index_from"] = kwargs["from_date"]
            captured["index_to"] = kwargs["to_date"]
            return pd.DataFrame({"asof_date": pd.to_datetime(["2026-03-23"])})

        def fake_roll_forward_index_constituent_stats(index_raw_df, target_date):
            captured["roll_forward_target"] = target_date
            return index_raw_df

        def fake_build_pivoted_index_df(_index_raw_df):
            return pd.DataFrame(
                {
                    "index_row_id": [1],
                    "index_asof_date": pd.to_datetime(["2026-03-23"]),
                }
            ), []

        def fake_match_factset_to_index(**kwargs):
            result = pd.DataFrame(
                {
                    "position_row_id": [1],
                    "report_date": pd.to_datetime(["2015-06-30"]),
                    "asof_date": pd.to_datetime(["2026-03-23"]),
                    "index_asof_date": pd.to_datetime(["2026-03-23"]),
                }
            )
            return result, pd.DataFrame(), result.copy(), result.copy()

        monkeypatch.setattr(
            "data_access.dbtables.bdc.factset_bdc_enriched.get_factset_bdc_holdings_asof",
            fake_get_factset_bdc_holdings_asof,
        )
        monkeypatch.setattr(enriched, "get_bbg_bridge", lambda: {})
        monkeypatch.setattr(
            "data_access.dbtables.bdc.factset_bdc_enriched.prepare_factset_df",
            fake_prepare_factset_df,
        )
        monkeypatch.setattr(
            "data_access.dbtables.bdc.factset_bdc_enriched.fetch_factset_holdings_knowledge",
            fake_fetch_factset_holdings_knowledge,
        )
        monkeypatch.setattr(
            "data_access.dbtables.bdc.factset_bdc_enriched.select_incremental_holdings_rows",
            fake_select_incremental_holdings_rows,
        )
        monkeypatch.setattr(
            "data_access.dbtables.bdc.factset_bdc_enriched.fetch_index_constituent_stats_direct",
            fake_fetch_index_constituent_stats_direct,
        )
        monkeypatch.setattr(
            "data_access.dbtables.bdc.factset_bdc_enriched.roll_forward_index_constituent_stats",
            fake_roll_forward_index_constituent_stats,
        )
        monkeypatch.setattr(
            "data_access.dbtables.bdc.factset_bdc_enriched.build_pivoted_index_df",
            fake_build_pivoted_index_df,
        )
        monkeypatch.setattr(
            "data_access.dbtables.bdc.factset_bdc_enriched.match_factset_to_index",
            fake_match_factset_to_index,
        )

        bundle = run_pipeline(
            from_date="2026-03-23",
            to_date="2026-03-23",
            run_asof_date="2026-03-23",
            filter_index_to_factset_keys=False,
            reuse_previous_enriched_snapshot=False,
        )

        assert captured["index_from"] == "2026-03-20"
        assert captured["index_to"] == "2026-03-23"
        assert captured["roll_forward_target"] == "2026-03-23"
        assert captured["holdings_knowledge_kwargs"]["update_from"] == "2026-03-23"
        assert captured["incremental_input_rows"] == 1
        qa = bundle["qa_summary"].iloc[0]
        assert qa["factset_snapshot_rows"] == 1
        assert qa["holdings_knowledge_rows"] == 1
        assert qa["changed_holdings_rows"] == 1

    def test_daily_mode_enriches_when_holdings_unchanged(self, monkeypatch):
        captured = {}

        monkeypatch.setattr(
            enriched,
            "get_factset_bdc_holdings_asof",
            lambda _asof_date: pd.DataFrame({"raw": [1]}),
        )
        monkeypatch.setattr(
            enriched,
            "prepare_factset_df",
            lambda _df, bbg_bridge=None: pd.DataFrame(
                {
                    "position_row_id": [1],
                    "report_date": pd.to_datetime(["2026-03-01"]),
                    "asof_date": pd.to_datetime(["2026-03-23"]),
                    "bdc_factset_entity_id": ["ENT1"],
                    "factset_report_id": [1],
                    "fsym_id": ["FSYM1"],
                }
            ),
        )
        monkeypatch.setattr(enriched, "get_bbg_bridge", lambda: {})
        monkeypatch.setattr(
            enriched,
            "fetch_factset_holdings_knowledge",
            lambda **_kwargs: pd.DataFrame(
                {
                    "holding_row_key": ["H1"],
                    "holding_row_hash": ["HASH1"],
                    "report_date": pd.to_datetime(["2026-03-01"]),
                }
            ),
        )
        monkeypatch.setattr(
            enriched,
            "select_incremental_holdings_rows",
            lambda **_kwargs: pd.DataFrame(columns=["holding_row_key", "holding_row_hash", "report_date"]),
        )
        monkeypatch.setattr(
            enriched,
            "fetch_index_constituent_stats_direct",
            lambda **_kwargs: pd.DataFrame({"asof_date": pd.to_datetime(["2026-03-23"])}),
        )
        monkeypatch.setattr(
            enriched,
            "roll_forward_index_constituent_stats",
            lambda index_raw_df, target_date: index_raw_df,
        )
        monkeypatch.setattr(
            enriched,
            "build_pivoted_index_df",
            lambda _index_raw_df: (
                pd.DataFrame({"index_row_id": [1], "index_asof_date": pd.to_datetime(["2026-03-23"])}),
                [],
            ),
        )

        def fake_match_factset_to_index(**kwargs):
            captured["matched"] = True
            result = pd.DataFrame(
                {
                    "position_row_id": [1],
                    "report_date": pd.to_datetime(["2026-03-01"]),
                    "asof_date": pd.to_datetime(["2026-03-23"]),
                    "index_asof_date": pd.to_datetime(["2026-03-23"]),
                }
            )
            return result, pd.DataFrame(), result.copy(), result.copy()

        monkeypatch.setattr(enriched, "match_factset_to_index", fake_match_factset_to_index)

        bundle = run_pipeline(
            from_date="2026-03-23",
            to_date="2026-03-23",
            run_asof_date="2026-03-23",
            filter_index_to_factset_keys=False,
            reuse_previous_enriched_snapshot=False,
        )

        qa = bundle["qa_summary"].iloc[0]
        assert captured["matched"] is True
        assert qa["changed_holdings_rows"] == 0
        assert qa["result_rows"] == 1


class TestPersistResult:
    @staticmethod
    def _result_df():
        return pd.DataFrame(
            {
                "position_row_id": [1],
                "report_date": pd.to_datetime(["2026-03-01"]),
                "asof_date": pd.to_datetime(["2026-03-23"]),
            }
        )

    @staticmethod
    def _target_probe_df():
        return pd.DataFrame(
            columns=[
                "position_row_id",
                "report_date",
                "asof_date",
                "collection_date",
                "last_update",
                "knowledge_date",
                "upload_date",
            ]
        )

    @pytest.mark.parametrize("partition_columns", [[], ["report_date"]])
    def test_parallel_shards_require_knowledge_date_partition(self, monkeypatch, partition_columns):
        monkeypatch.setattr(enriched, "_get_delta_partition_columns", lambda *_args, **_kwargs: partition_columns)

        with pytest.raises(RuntimeError, match="partitioned by knowledge_date"):
            enriched.validate_parallel_result_target("dbo.result")

    def test_parallel_shards_accept_knowledge_date_partition(self, monkeypatch):
        monkeypatch.setattr(
            enriched,
            "_get_delta_partition_columns",
            lambda *_args, **_kwargs: ["knowledge_date"],
        )

        enriched.validate_parallel_result_target("dbo.result")

    @pytest.mark.parametrize("partition_columns", [["report_date"], []])
    def test_existing_target_uses_atomic_slice_replacement(self, monkeypatch, partition_columns):
        statements = []
        replacements = []
        monkeypatch.setattr(enriched.sparkmanager, "isinit", True, raising=False)
        monkeypatch.setattr(enriched.sparkmanager, "blob_dloader", object(), raising=False)
        monkeypatch.setattr(enriched.sparkmanager, "refresh_spark", lambda: None)

        def read(sql, **kwargs):
            if sql.strip().upper().startswith("DESCRIBE DETAIL"):
                return pd.DataFrame({"partitionColumns": [partition_columns]})
            return self._target_probe_df()

        monkeypatch.setattr(enriched, "_read_bdc_databricks_sql", read)
        monkeypatch.setattr(enriched, "_execute_bdc_databricks_sql", lambda sql, **_: statements.append(sql))
        monkeypatch.setattr(enriched, "_replace_spark_result_slice",
                            lambda out, table, predicate: replacements.append((table, predicate)))
        enriched.persist_result(
            result=self._result_df(), from_date="2026-03-23", to_date="2026-03-23",
            sql_table_name="dbo.result", delta_table_name="result", run_write=True,
            delete_by_knowledge_date=True, use_spark_write=True,
        )
        assert not any("DELETE FROM" in sql for sql in statements)
        assert replacements == [("dbo.result", "CAST(knowledge_date AS DATE) >= DATE('2026-03-23') "
                                              "AND CAST(knowledge_date AS DATE) <= DATE('2026-03-23')")]


# ---------------------------------------------------------------------------
# crosswalk_multivariant_enrichment.run_multivariant_join_enrichment signature
# ---------------------------------------------------------------------------
class TestRunMultivariantSignature:
    def test_importable(self):
        from data_access.dbtables.bdc.crosswalk_multivariant_enrichment import (
            run_multivariant_join_enrichment,
        )
        import inspect
        sig = inspect.signature(run_multivariant_join_enrichment)
        params = set(sig.parameters.keys())
        expected = {
            "from_date", "to_date", "index_view", "chunk_months",
            "use_chunked_reads", "enable_parallel_io", "max_parallel_workers",
            "max_parallel_key_workers", "databricks_app", "run_write",
            "bootstrap", "sql_table_name", "delta_table_name", "delta_blob_dir",
            "index_roll_forward_days",
        }
        assert expected.issubset(params), f"Missing params: {expected - params}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


def test_empty_result_cannot_mutate_persisted_snapshot(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Empty results must fail before accessing persistence")

    monkeypatch.setattr(enriched.sparkmanager, "init", unexpected)
    monkeypatch.setattr(enriched, "_execute_bdc_databricks_sql", unexpected)
    with pytest.raises(RuntimeError, match="empty result"):
        enriched.persist_result(pd.DataFrame(), "2026-09-09", "2026-09-09", run_write=True)


def test_daily_pipeline_fails_when_all_index_sources_are_empty(monkeypatch):
    holdings_df = pd.DataFrame({"report_date": pd.to_datetime(["2026-06-30"])})
    monkeypatch.setattr(enriched, "fetch_factset_holdings_knowledge", lambda **_: pd.DataFrame())
    monkeypatch.setattr(enriched, "select_incremental_holdings_rows", lambda **_: pd.DataFrame())
    monkeypatch.setattr(enriched, "get_factset_bdc_holdings_asof", lambda _: holdings_df)
    monkeypatch.setattr(enriched, "get_bbg_bridge", lambda: {})
    monkeypatch.setattr(enriched, "prepare_factset_df", lambda df, **_: df.copy())
    monkeypatch.setattr(enriched, "fetch_index_constituent_stats_direct", lambda **_: pd.DataFrame())
    with pytest.raises(RuntimeError, match="No index rows available"):
        enriched.run_pipeline(
            "2026-09-09", "2026-09-09", run_asof_date="2026-09-09",
            reuse_previous_enriched_snapshot=False,
        )
