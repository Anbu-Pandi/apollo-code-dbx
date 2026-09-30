"""Seed apo_config with the wiki sub-server catalog defaults.

Run once per environment::

    python -m dash_reports.sub_servers.argus_wiki_server.seed_app_config --env dev --memory-limit 0

Idempotent - existing values are not overwritten unless ``--force`` is passed.
Use ``--dry-run`` to preview.
"""
from __future__ import annotations

import logging

import numpy as np

from apo_config.config import config as apo
from apo_config.configdb import configdb
from common.utils.envsetup import get_base_parser, init_app_using_parser
from dash_reports.sub_servers.argus_wiki_server.config_catalog import APP, CATALOG


def main() -> None:
    parser = get_base_parser("argus_wiki_seed_app_config")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing values that differ from the catalog.")
    args = init_app_using_parser(parser)

    env = apo.getenv()
    for key, spec in CATALOG.items():
        value = spec.get(env)
        if value is None:
            logging.info("skip %s no value for env=%s", key, env)
            continue
        current = apo.getconfig(APP, key)
        if str(current or "") == str(value or ""):
            logging.info("ok %s unchanged (=%s)", key, value)
            continue
        if current and not args.force:
            logging.info("keep %s=%s (catalog=%s) - pass --force to overwrite", key, current, value)
            continue
        if args.dry_run:
            logging.info("would set %s=%s was=%s", key, value, current)
            continue
        configdb.saveappconfigkey(env, APP, np.nan, key, value, isuseroverride=False)
        logging.info("set %s=%s was=%s", key, value, current)


if __name__ == "__main__":
    main()
