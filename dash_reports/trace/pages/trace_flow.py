from dash_reports.report_common.data.data_model import datastore
from dash_reports.report_common.pages.pivot_page_helpers import create_pivot_controls


def create_layout():
    df = datastore.get_trace_history()

    def get_df():
        return df

    show_value_columns = ['Net Quantity', 'Gross Quantity']
    show_value_agg_func = {x: ['sum'] for x in show_value_columns}
    return create_pivot_controls(False, 'TRACE Flow Summary (in millions)', get_df,
                                 rows=['Sector', ],
                                 columns=['Trade Counterparties', 'Yield Bucket'],
                                 values=show_value_columns,
                                 aggfunc=show_value_agg_func,
                                 filtercolumns=['Week Ending Date'],
                                 filtervalues=[df['Week Ending Date'].max().strftime('%Y-%m-%d')])
