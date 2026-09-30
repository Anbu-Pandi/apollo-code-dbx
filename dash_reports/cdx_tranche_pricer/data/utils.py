import base64
import io
import logging

import pandas as pd


def parse_contents(contents, filename):
    content_type, content_string = contents.split(',')
    decoded = base64.b64decode(content_string)
    try:
        assert 'csv' in filename, 'Only CSV files are supported'
        df = pd.read_csv(io.StringIO(decoded.decode('utf-8')))
        df = df.dropna(how='all')
        return df
    except Exception as e:
        logging.warning(f'error: {e}')
