import logging

import numpy as np

from apo_config.config import config as apo
from apo_config.configdb import configdb
from common.utils.envsetup import get_base_parser, init_app_using_parser
from dash_reports.sub_servers.vc_wiki.config_catalog import APP, CATALOG


def main() -> None:
    parser = get_base_parser("vc_wiki_seed_app_config")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = init_app_using_parser(parser)

    env = apo.getenv()
    configdb.init(env, apo.getuser(actual=True))
    for key, spec in CATALOG.items():
        value = spec.get(env)
        if value is None:
            continue
        current = apo.getconfig(APP, key)
        if str(current or "") == str(value or ""):
            logging.info("ok %s unchanged (=%s)", key, value)
            continue
        if current and not args.force:
            logging.info("keep %s=%s (catalog=%s) - pass --force", key, current, value)
            continue
        if args.dry_run:
            logging.info("would set %s=%s was=%s", key, value, current)
            continue
        configdb.saveappconfigkey(env, APP, np.nan, key, value, isuseroverride=False)
        logging.info("set %s=%s was=%s", key, value, current)


if __name__ == "__main__":
    main()
