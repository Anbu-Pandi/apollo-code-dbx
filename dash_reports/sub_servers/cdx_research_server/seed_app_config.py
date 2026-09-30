import logging

from apo_config.config import config
from apo_config.configdb import configdb
from common.utils.envsetup import get_base_parser, init_app_using_parser
from dash_reports.sub_servers.cdx_research_server.config_catalog import APP, CATALOG


def main():
    parser = get_base_parser('cdx_research_seed_app_config')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--force', action='store_true')
    args = init_app_using_parser(parser)
    configdb.init(args.env, config.getuser(actual=True))
    with configdb.get_write_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT configkey, configvalue FROM dbo.app_config '
                           'WHERE env = %s AND app = %s AND username IS NULL', [args.env, APP])
            existing = dict(cursor.fetchall())
    for key, spec in CATALOG.items():
        value, current = spec[args.env], existing.get(key)
        if current == value or (current is not None and not args.force):
            logging.info('keep %s.%s=%s', APP, key, current)
        elif args.dry_run:
            logging.info('would set %s.%s=%s was=%s', APP, key, value, current)
        else:
            configdb.saveappconfigkey(args.env, APP, None, key, value)
            logging.info('set %s.%s=%s was=%s', APP, key, value, current)


if __name__ == '__main__':
    main()
