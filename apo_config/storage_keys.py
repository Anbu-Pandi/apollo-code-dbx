import getpass
import sys

from apo_config.configdb import _read_runtime_config


def main():
    config = _read_runtime_config(getpass.getuser().lower())
    sys.stdout.write(
        f"devKey={config['dev']['password']}\n"
        f"prodKey={config['prod']['password']}\n"
    )


if __name__ == '__main__':
    main()
