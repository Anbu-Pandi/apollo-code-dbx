from dash_reports.report_common.elastic_search.api import default_es
from elasticsearch import Elasticsearch, helpers

ridxs = ['analyst_issuer_corp_tree', 'analyst_ques_debt', 'analyst_ques_mgmt', 'analyst_ques_acquisition',
         'analyst_ques_equity', 'analyst_issuer_coverage', 'analyst_ques_ratings', 'analyst_ques_peers',
         'analyst_questions']
idxs = ['analystissuercorptree', 'analystquesdebt', 'analystquesmgmt', 'analystquesacquisition', 'analystquesequity',
        'analystissuercoverage', 'analystquesratings', 'analystquespeers', 'analystquestions']

for i,r in zip(idxs, ridxs):
    helpers.reindex(client=default_es._es, source_index=i, target_index=r)
    default_es.delete_index(i)