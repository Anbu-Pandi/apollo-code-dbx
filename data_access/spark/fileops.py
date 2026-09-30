import logging
import os.path

from common.utils.envsetup import get_base_parser, init_app_using_parser
from data_access.spark.api import sparkmanager

CMD = ['cp', 'mv', 'ls', 'rm', 'mkdirs']


def _abs_path(p):
    p = p or ''
    if (p[:4].lower() != 'dbfs') and (p[:5].lower() != 'abfss'):
        p = os.path.abspath(p)
    if (p[:9].lower() == 'abfss_rel'):
        p = sparkmanager._getfilepath(p[10:])
    return p


def process_cmd(cmd, source=None, dest=None):
    cmd = str(cmd or '').lower()
    source = _abs_path(source)

    if cmd == 'cp':
        assert dest is not None, 'dest cannot be None'
        dest = _abs_path(dest)
        logging.info(f"cp'ing source: {source}, dest: {dest}")
        if not (source.startswith('dbfs') or source.startswith('abfss')):
            source = f'file:///{source}'
        if not (dest.startswith('dbfs') or dest.startswith('abfss')):
            dest = f'file:///{dest}'
        retval = sparkmanager.get_dbutils().fs.cp(source, dest, recurse=True)
    elif cmd == 'mv':
        assert dest is not None, 'dest cannot be None'
        dest = _abs_path(dest)
        logging.info(f"mv'ing source: {source}, dest: {dest}")
        if not (source.startswith('dbfs') or source.startswith('abfss')):
            source = f'file:///{source}'
        if not (dest.startswith('dbfs') or dest.startswith('abfss')):
            dest = f'file:///{dest}'
        retval = sparkmanager.get_dbutils().fs.mv(source, dest, recurse=True)
    elif cmd == 'ls':
        logging.info(f"ls'ing source: {source}")
        retval = sparkmanager.get_dbutils().fs.ls(source)
    elif cmd == 'mkdirs':
        logging.info(f"mkdirs'ing source: {source}")
        retval = sparkmanager.get_dbutils().fs.mkdirs(source)
    elif cmd == 'rm':
        logging.info(f"rm'ing source: {source}")
        retval = sparkmanager.get_dbutils().fs.rm(source, recurse=True)
    else:
        raise Exception(f'cmd has to be: {",".join(CMD)}')
    return retval


def getargparser():
    import uuid
    parser = get_base_parser('fileops', log_file=f'fileops_{uuid.uuid4()}')
    parser.add_argument('--cmd', required=True, default=False,
                        help=f'cmd: {",".join(CMD)}')
    parser.add_argument('--source', required=True, default=None,
                        help='source folder or path')
    parser.add_argument('--dest', required=False, default=None,
                        help='dest folder for cp cmd')

    return parser


if __name__ == '__main__':
    argparser = getargparser()
    args = argparser.parse_args()

    init_app_using_parser(argparser, configoverrides={'driver': 'sql'})
    sparkmanager.init()
    process_cmd(args.cmd, args.source, args.dest)
