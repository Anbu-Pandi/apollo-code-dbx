import seaborn as sns
import pdfkit
import os
import pandas as pd


class CreatePdf:
    def __init__(self, report_name, report_path, page_break=True):
        """
        :type report_name: name of the file
        :type report_path: destination where to save file
        """
        self.page_break = page_break
        self.report_name = report_name + '.pdf'
        self.report_path = report_path
        self.styles = [self.hover(),
                       dict(selector="th",
                            props=[("font-size", "80%"), ("text-align", "center"), ("vertical-align", "top"),
                                   ("background-color", "#4CAF50"), ("border", "1px solid #ddd"),
                                   ("color", "white")]),
                       dict(selector="td", props=[("text-align", "right"), ("border", "1px solid #ddd")]),
                       dict(selector="", props=[('border', '1px solid black')]),
                       dict(selector="tr:nth-child(even)", props=[("background-color", "#f2f2f2")]),
                       dict(selector="table", props=[("border-collapse", "collapse"),
                                                     ("font-family",
                                                      ["Trebuchet MS", "Arial", "Helvetica", "sans-serif"])])
                       ]
        self.path_wkthmltopdf = r'C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe'
        self.config = pdfkit.configuration(wkhtmltopdf=self.path_wkthmltopdf)
        self.wkhtmltopdfOptions = {
            'page-size': 'A4',
            #                                        'margin-top': '0.75in',
            #                                        'margin-right': '0.75in',
            #                                        'margin-bottom': '0.75in',
            #                                        'margin-left': '0.75in',
            #                                        'orientation': 'Landscape'
        }
        self.cssStyle = '<style  type="text/css" >\
                                    thead { display: table-header-group }\
                                    tfoot { display: table-row-group }\
                                    tr { page-break-inside: avoid }\
                                    //table {font-size: 12px;}\
                                </style>'


    def hover(hover_color="#ffff99"):
        return dict(selector="tr:hover",
                    props=[("background-color", "%s" % hover_color)])

    def style_df(self, df_dict):
        """df_dict: where key is caption of the table and value is the dataframe"""
        styled_df_dict = {}

        for key, df in df_dict.items():
            styled_df = df.style.set_table_styles(self.styles).set_caption(key)
            styled_df_dict[key] = styled_df

        print("Dataframe Styling Completed")
        return styled_df_dict

    def render_html(self, df_dict):
        html_code = ' '

        for key, df in df_dict.items():
                if self.page_break:
                    html_code = html_code + ' ' + df.render() + '<P style="page-break-before: always">'
                else:
                    html_code = html_code + ' ' + df.render() + "<P> <br>"

        html_code = self.cssStyle + "\n" + html_code
        print("html code rendered")

        return html_code


    def to_pdf(self, html_code):

        report = os.path.join(self.report_path, self.report_name)
        pdfkit.from_string(html_code, report,
                           configuration=self.config,
                           options=self.wkhtmltopdfOptions)

    def generate_pdf(self, df_dict):
        """df_dict: where key is caption of the table and value is the dataframe"""

        df_style = self.style_df(df_dict)
        html_code = self.render_html(df_style)
        self.to_pdf(html_code)

        print("PDF Published")


if __name__ == '__main__':
    cars = {'Brand': ['Honda Civic', 'Toyota Corolla', 'Ford Focus', 'Audi A4'],
            'Price': [22000, 25000, 27000, 35000]
            }

    bikes = {'Brand': ['Civic', 'Corolla', 'Focus', 'A4'],
             'Price': [22000, 25000, 27000, 35000]
             }

    df = pd.DataFrame(cars, columns=['Brand', 'Price'])
    df2 = pd.DataFrame(bikes, columns=['Brand', 'Price'])

    obj = CreatePdf('test', r'H:\CodeTrash', True)
    obj.generate_pdf({'Cars': df, 'bikes': df2})




