from dash import Dash

from common.utils.envsetup import init_app_using_parser
from dash_reports.sponsor_study.pages.sponsor_study_page import create_layout


def main():
    init_app_using_parser()
    app = Dash(__name__)
    app.title = "Live Sponsor Study"
    app.layout = create_layout()
    app.run_server(debug=False, port=8066)


if __name__ == "__main__":
    main()

