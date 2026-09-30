import re


def is_float(s):
    int_pattern = re.compile(r'^-?\d+(\.\d+)?$')
    return re.match(int_pattern, str(s))


def string_to_int(lst):
    return [int(float(str(x))) for x in lst if is_float(x)]


def string_to_float(lst):
    return [float(str(x)) for x in lst if is_float(x)]


def xlstring_to_float(x):
    if isinstance(x, float):
        return x

    x_str = str(x)
    x_str = x_str.replace("'-", '-')
    if x_str in ('', '-'):
        return float('NaN')

    sign = -1.0 if '(' in x_str else 1.0

    translator = str.maketrans('', '', '(),+')
    x_str = x_str.translate(translator)

    if '%' in x_str:
        x_str = x_str.strip('%').replace("'", '')
        return sign * float(x_str) / 100

    return sign * float(x_str)


if __name__ == '__main__':
    import logging
    from common.utils.logutil import setuplogger

    setuplogger()
    x = ['0.0', '0.000adf', 'asdfa', 'aa10.0', '10', '10.01', '0.0.0', '0..0']
    logging.info(string_to_int(x))

    logging.info(string_to_float(x))
