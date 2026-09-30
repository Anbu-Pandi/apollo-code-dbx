import logging
from collections import Counter


def main():
    from common.utils.envsetup import get_base_parser, init_app_using_parser
    from dash_reports.subscriptions.runtime import get_service
    parser = get_base_parser("dash_email_subscriptions")
    init_app_using_parser(parser)
    outcomes = Counter(get_service().run_due().values())
    logging.info("Email subscription delivery outcomes: %s", dict(outcomes))
    if outcomes.get("failed") or outcomes.get("access_denied"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
