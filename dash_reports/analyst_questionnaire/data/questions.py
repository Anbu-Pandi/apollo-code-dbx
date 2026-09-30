from dash_reports.report_common.elastic_search.api import default_es


def get_questions():
    from dash_reports.analyst_questionnaire.data.caching_const import ES_QUESTIONS
    return default_es.search_all(ES_QUESTIONS)


def get_section_questions(section):
    df = get_questions()
    return df[df['section'] == section].sort_values('order')
