import logging

CATALOG = {
    "email_subscriptions_root": {"dev": "//pue2mal02/e/apo_apps/state/email_subscriptions/dev",
                                 "prod": "//pue2mal02/e/apo_apps/state/email_subscriptions/prod"},
    "email_subscription_providers": {env: "dash_reports.vc_dashboard.subscriptions" for env in ("dev", "prod")},
    "email_subscriptions_url": {env: "https://argus.apollo.com" for env in ("dev", "prod")},
    "email_subscriptions_sender": {env: "agite@apollo.com" for env in ("dev", "prod")},
    "email_subscriptions_smtp_host": {env: "mail.apollolp.com" for env in ("dev", "prod")},
}


def main():
    import numpy as np
    from apo_config.config import config
    from apo_config.configdb import configdb
    from common.utils.envsetup import get_base_parser, init_app_using_parser
    parser = get_base_parser("dash_email_subscriptions_seed")
    parser.add_argument("--dry-run", action="store_true")
    args = init_app_using_parser(parser)
    configdb.init(args.env, config.getuser(actual=True))
    for key, values in CATALOG.items():
        current = config.getconfig("dash_reports", key)
        if current:
            logging.info("Keep existing %s", key)
        elif args.dry_run:
            logging.info("Would configure %s=%s", key, values[args.env])
        else:
            configdb.saveappconfigkey(args.env, "dash_reports", np.nan, key, values[args.env], isuseroverride=False)
            logging.info("Configured %s", key)


if __name__ == "__main__":
    main()
