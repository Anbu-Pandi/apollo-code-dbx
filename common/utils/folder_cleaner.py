import datetime
import logging
import os

from common.utils import dtutil
from common.utils.envsetup import get_base_parser, init_app_using_parser


def get_argparser():
    parser = get_base_parser(os.path.split(__file__)[-1], log_file=os.path.split(__file__)[-1] + '.log')
    parser.add_argument('--folder_tenor_pairs', nargs='+', help='folder tenor folder tenor...')
    parser.add_argument('--base-date', default=None,
                        help='Base date for calculating cutoff date (default: today). Format: YYYY-MM-DD. ')
    parser.add_argument('--dry-run', action='store_true')
    return parser


def clean_files_in_folder(folder_path, cutoff_date, dry_run=False):
    if not os.path.exists(folder_path):
        logging.warning(f"No folder: {folder_path}")
        return 0, 0, [f"No folder: {folder_path}"]

    files_checked = files_deleted = 0
    errors = []

    for root, dirs, files in os.walk(folder_path):
        for file in files:
            files_checked += 1
            file_path = os.path.join(root, file)

            try:
                if datetime.datetime.fromtimestamp(os.path.getmtime(file_path)).date() < dtutil.todate(cutoff_date):
                    if dry_run:
                        logging.info(f"Would delete: {file_path}")
                    else:
                        logging.info(f"Deleting: {file_path}")
                        try:
                            os.remove(file_path)
                        except Exception as e:
                            error_msg = f"Failed to delete: {file_path}: {str(e)}"
                            logging.error(error_msg)
                            errors.append(error_msg)
                            continue
                    files_deleted += 1
            except Exception as e:
                error_msg = f"Error checking: {file_path}: {str(e)}"
                logging.error(error_msg)
                errors.append(error_msg)

    if not dry_run:
        for root, dirs, files in os.walk(folder_path, topdown=False):
            for dir_name in dirs:
                dir_path = os.path.join(root, dir_name)
                if not os.listdir(dir_path):
                    logging.info(f"Removing empty dir: {dir_path}")
                    try:
                        os.rmdir(dir_path)
                    except Exception as e:
                        error_msg = f"Failed to remove dir: {dir_path}: {str(e)}"
                        logging.error(error_msg)
                        errors.append(error_msg)

    return files_checked, files_deleted, errors


def run_file_cleaner(args):
    if len(args.folder_tenor_pairs) % 2 != 0:
        raise ValueError("Must provide pairs of folder and tenor")

    if args.base_date is None:
        base_date = dtutil.today()
    base_date = dtutil.todate(base_date)
    folders = args.folder_tenor_pairs[::2]
    tenors = args.folder_tenor_pairs[1::2]

    total_checked = total_deleted = 0
    all_errors = []

    for folder, tenor in zip(folders, tenors):
        cutoff_date = dtutil.dateadd(base_date, f"-{tenor}")
        logging.info(f"Cleaning {folder} with tenor {tenor} (cutoff: {cutoff_date})")
        checked, deleted, errors = clean_files_in_folder(folder, cutoff_date, args.dry_run)
        total_checked += checked
        total_deleted += deleted
        all_errors.extend(errors)

    logging.info(f"Total: checked {total_checked}, {'would delete' if args.dry_run else 'deleted'} {total_deleted}")

    if all_errors:
        logging.warning(f"Encountered {len(all_errors)} errors")
        for error in all_errors[:10]:  # Limit output to first 10 errors
            logging.warning(error)
        if len(all_errors) > 10:
            logging.warning(f"...and {len(all_errors) - 10} more errors")


if __name__ == "__main__":
    argparser = get_argparser()
    init_app_using_parser(argparser)
    args, _ = argparser.parse_known_args()
    run_file_cleaner(args)
