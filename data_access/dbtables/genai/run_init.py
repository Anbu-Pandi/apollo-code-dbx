from common.utils.envsetup import init_app_using_parser
from data_access.dbtables.genai.init_tables import get_parser, init_genai_tables

if __name__ == '__main__':
    args = init_app_using_parser(get_parser())
    init_genai_tables(
        overwrite=args.overwrite,
        include_conversation_logs=args.table in ('all', 'conversation_logs'),
        include_run_audit=args.table in ('all', 'run_audit'),
    )

