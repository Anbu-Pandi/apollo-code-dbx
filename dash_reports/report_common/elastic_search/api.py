import logging
import sys
import uuid

import numpy as np
import pandas as pd
from elasticsearch import Elasticsearch, helpers

from apo_config.config import config
from apo_config.config import required_config
from common.utils.dfutil import tolist, chunks_df, chunks_list

ES_STR_NA_REPLACEMENT = '#n/a'
ES_DT_NA_REPLACEMENT = pd.to_datetime('1900-01-01')
ES_NUM_NA_REPLACEMENT = sys.maxsize
DEFAULT_SEARCH_SIZE = 25
FULL_SEARCH = 5_000
SEARCH_BATCH_SIZE = 1000
TIMEOUT = 60 * 4

TEXT_WITH_KEYWORD_SCHEMA = {'type': 'text',
                            'fields': {'keyword': {'type': 'keyword',
                                                   'normalizer': 'lowercase_normalizer',
                                                   'ignore_above': 256}}}
DATE_SCHEMA = {'type': 'date',
               'format': "yyy-MM-dd HH:mm:ss||yyyy-MM-dd||yyyy-MM-dd'T'HH:mm:ss"}

LOWERCASE_NORMALIZER = {"analysis": {"normalizer": {"lowercase_normalizer": {"type": "custom",
                                                                             "filter": ["lowercase"]}}}}


class ElasticSearchManager:
    _es = None

    def __init__(self, app=None, lazy=True):
        self._app = app
        self._es_server = None
        self._lazy = lazy
        self._initialized = False
        if not lazy:
            self._init_connection()

    def _init_connection(self):
        if self._initialized:
            return
        self._es_server = (
            config.getconfig(self._app, 'ELASTIC_SEARCH_SERVER')
            or required_config('dash_reports', 'elastic_search_server')
        )
        self._es = Elasticsearch(self._es_server, request_timeout=TIMEOUT)
        self._initialized = True
        try:
            if self._es.ping():
                logging.info(f'successfully connected to: {self._es_server}')
            else:
                logging.warning(f'Error connecting to server: {self._es_server}')
        except Exception as e:
            logging.warning(f'Error connecting to server: {self._es_server} - {e}')

    def _ensure_connected(self):
        if not self._initialized:
            self._init_connection()

    def _bulk_add(self, actions):
        self._ensure_connected()
        logging.info('starting bulk add')
        helpers.bulk(self._es, actions)
        logging.info('completed bulk add')

    def search_prefix(self, index, fields, search_val, size=DEFAULT_SEARCH_SIZE, get_score=False):
        query_dict = self._gen_query(index, 'prefix', fields, search_val, size=size)
        return self._query(index, query_dict, get_score=get_score)

    def search_fuzzy(self, index, fields, search_val, size=DEFAULT_SEARCH_SIZE, get_score=False, **kwargs):
        query_dict = self._gen_query(index, 'fuzzy', fields, search_val, size=size, **kwargs)
        return self._query(index, query_dict, get_score=get_score)

    def search_exact(self, index, fields, search_val, get_score=False):
        dfs = []
        for sv in chunks_list(tolist(search_val, replacenone=True), SEARCH_BATCH_SIZE):
            query_dict = self._gen_query(index, 'exact', fields, sv)
            dfi = self._query(index, query_dict, get_score=get_score)
            dfs.append(dfi)
        if len(dfs) > 0:
            df = pd.concat(dfs)
        else:
            df = pd.DataFrame()
        return df

    def search_exact_one(self, index, fields, search_val, get_score=False):
        query_dict = self._gen_query(index, 'exact_one', fields, search_val)
        return self._query(index, query_dict, get_score=get_score)

    def search_all(self, index, size=FULL_SEARCH, get_score=False, **kwargs):
        query_dict = self._gen_query(index, 'all', size=size, **kwargs)
        return self._query(index, query_dict, get_score=get_score)

    @staticmethod
    def res_to_df(res, get_score=False):
        matches = res['hits']['hits']
        df = pd.DataFrame([x['_source'] for x in matches], index=[x['_id'] for x in matches])
        if (len(df) > 0) and get_score:
            score_df = pd.DataFrame([x['_score'] for x in matches],
                                    index=[x['_id'] for x in matches],
                                    columns=['_fuzzy_score'])
            df = pd.merge(df, score_df, how='left', left_index=True, right_index=True)
        return df

    @staticmethod
    def _gen_prefix_query(search_values, search_fields):
        query = {
            "bool": {
                "should": [
                ]
            }
        }

        for value in search_values:
            for field in search_fields:
                query["bool"]["should"].append({"match_phrase_prefix": {field: value}})

        return query

    def _gen_query(self, index_name, query_type, fields=None, search_val=None, **kwargs):
        logging.info(f'generating query for: {query_type}\n'
                     f'fields:{len(tolist(fields, replacenone=True))}\n'
                     f'search_val:{len(tolist(search_val, replacenone=True))}')
        allowed_query_types = ['fuzzy', 'prefix', 'exact', 'exact_one', 'all']
        query_type = (query_type or '').lower()
        assert query_type in allowed_query_types, f'{query_type} not in {allowed_query_types}'
        match_dict = {}

        size = FULL_SEARCH
        if 'size' in kwargs:
            size = kwargs['size']
            kwargs.pop('size')

        if query_type == 'fuzzy':
            if not 'fuzziness' in kwargs:
                kwargs['fuzziness'] = 'AUTO'
            field_dict = {'fields': fields, 'query': search_val, }
            field_dict.update(kwargs)
            match_dict = {'multi_match': field_dict}

        elif query_type == 'prefix':
            match_dict = ElasticSearchManager._gen_prefix_query(tolist(search_val), tolist(fields))

        elif query_type.startswith('exact'):
            fields = tolist(fields)
            # Check if .keyword variant of the fields exist and replace them
            fields = self.check_keyword_fields(index_name, fields)

            search_val = tolist(search_val, replacenone=True)
            # We won't be adding quotes for term queries, simply lowercasing the values
            search_val = [str(x).lower() for x in search_val if (pd.notnull(x) and str(x).lower() != 'none')]

            # Construct sub queries
            field_sub_queries = []
            for field in fields:
                field_sub_queries.append({"terms": {field: search_val}})

            match_dict = {
                "bool": {
                    "must": field_sub_queries
                }
            }

            if query_type == 'exact_one':
                size = 1

        elif query_type == 'all':
            match_dict = {'match_all': {}}

        query_dict = {'query': match_dict, 'size': size}
        if 'sort' in kwargs:
            query_dict.update({'sort': kwargs['sort']})

        logging.info(f'generated query: {query_dict} for query_type: {query_type}')
        return query_dict

    def check_keyword_fields(self, index_names, fields):
        self._ensure_connected()
        properties = {}
        for index_name in tolist(index_names):
            mapping = self._es.indices.get_mapping(index=index_name)
            properties.update(mapping[index_name].get('mappings', {}).get('properties', {}))

        # Replace fields with their .keyword counterpart if they exist
        return [f'{x}.keyword'
                if properties[x].get('fields', {}).get('keyword', None)
                else x for x in fields]

    def _query(self, index, query_dict, get_score=False):
        self._ensure_connected()
        if not self.has_index(index):
            return pd.DataFrame()

        logging.info(f'request to retrieve index: {index} for query: {query_dict}')
        kwargs = {}
        if ('size' not in query_dict) or (query_dict['size'] == FULL_SEARCH):
            query_dict['size'] = FULL_SEARCH
            kwargs = dict(scroll='1m')
        res = self._es.search(index=index,
                              body=query_dict,
                              timeout='60s',
                              **kwargs)
        df = ElasticSearchManager.res_to_df(res, get_score=get_score)

        while ('_scroll_id' in res) and (len(res['hits']['hits']) != 0):
            res = self._es.scroll(scroll_id=res['_scroll_id'], scroll='1m')
            df = pd.concat([df, ElasticSearchManager.res_to_df(res, get_score=get_score)])

        if '_scroll_id' in res:
            self._es.clear_scroll(scroll_id=res['_scroll_id'])

        logging.info(f'retrieved index: {index} df: {df.shape}')
        df = ElasticSearchManager._null_outfrom_es(df)

        return df

    def _null_into_es(self, df, index_name):
        self._ensure_connected()
        str_cols = []
        dt_cols = []

        current_mapping = self._es.indices.get_mapping(index=index_name)
        properties = current_mapping[index_name]['mappings']['properties']

        for field, definition in properties.items():
            if (definition['type'] == 'text') and (field in df.columns):
                str_cols.append(field)
            if (definition['type'] == 'date') and (field in df.columns):
                dt_cols.append(field)

        num_cols = list(set(df.columns.tolist()) - set(str_cols) - set(dt_cols))

        df.replace([None, np.inf, -np.inf], np.nan, inplace=True)
        df[str_cols] = df[str_cols].replace({None: np.nan}).fillna(ES_STR_NA_REPLACEMENT).astype(str)
        df[dt_cols] = df[dt_cols].replace({None: np.nan}).fillna(ES_DT_NA_REPLACEMENT)
        df[num_cols] = df[num_cols].replace({None: np.nan}).fillna(ES_NUM_NA_REPLACEMENT)
        return df

    @staticmethod
    def _null_outfrom_es(df):
        df.replace({ES_STR_NA_REPLACEMENT: np.nan, ES_DT_NA_REPLACEMENT: np.nan, ES_NUM_NA_REPLACEMENT: np.nan},
                   inplace=True)
        return df

    @staticmethod
    def _get_df_schema(df):
        str_cols = df.select_dtypes(exclude=['number', 'datetime']).columns
        dt_cols = df.select_dtypes(include=['datetime']).columns
        num_cols = df.select_dtypes(include=['number']).columns

        schema = {}
        schema.update({x: TEXT_WITH_KEYWORD_SCHEMA for x in str_cols})
        schema.update({x: DATE_SCHEMA for x in dt_cols})
        schema.update({x: {'type': 'float'} for x in num_cols})
        return schema

    def add_df(self, df, index, dfindex_cols=None, op_type='index'):
        self._ensure_connected()
        df = df.loc[:, ~df.columns.duplicated()]

        logging.info(f'adding index: {index} df: {df.shape}')
        if not self.has_index(index):
            logging.info(f'creating index: {index} df: {df.shape}')
            self._es.indices.create(index=index, settings=LOWERCASE_NORMALIZER)
            self._es.indices.put_mapping(index=index,
                                         body={'properties': ElasticSearchManager._get_df_schema(df)})

        df = self._null_into_es(df, index)

        def indexify(df):
            for i, dfrow in df.iterrows():
                if dfindex_cols is not None:
                    i = '_'.join([str(x) for x in dfrow[dfindex_cols]])
                index_dict = {"_index": index, "_id": i, '_op_type': op_type, }
                source_or_doc = '_source'
                if op_type != 'index':
                    source_or_doc = 'doc'
                    index_dict['doc_as_upsert'] = True
                index_dict[source_or_doc] = dfrow.to_dict()
                yield index_dict

        for dfi in chunks_df(df, 100_000):
            self._bulk_add(indexify(dfi))

        logging.info(f'completed adding index: {index} df: {df.shape}')

    def has_index(self, index):
        self._ensure_connected()
        return self._es.indices.exists(index=index)

    def delete_index(self, index):
        self._ensure_connected()
        if self._es.indices.exists(index=index):
            logging.info(f'deleting index: {index}')
            self._es.indices.delete(index=index)

    def get_cached_df(self, index, create_df_func=None, refresh_cache=False):
        if refresh_cache:
            assert create_df_func is not None, "cannot refresh cache without create_df_func!"

        logging.info(f'get cache index: {index}')
        if default_es.has_index(index) and not refresh_cache:
            df = default_es.search_all(index)
            return df

        logging.info(f'cache miss for index: {index}')
        if not create_df_func is None or refresh_cache:
            df = create_df_func()
            if refresh_cache:
                default_es.replace_index(index, df)
            else:
                default_es.add_df(df, index)
            return df

    def replace_index(self, alias, df):
        self._ensure_connected()
        physical = f"{alias}__{uuid.uuid4().hex}"
        self.add_df(df, physical)
        actions = []
        old_indices = []
        if self._es.indices.exists_alias(name=alias):
            old_indices = list(self._es.indices.get_alias(name=alias))
            actions.extend(
                {"remove": {"index": index, "alias": alias}}
                for index in old_indices
            )
        elif self._es.indices.exists(index=alias):
            old_indices = [alias]
            actions.append({"remove_index": {"index": alias}})
        actions.append({"add": {"index": physical, "alias": alias}})
        self._es.indices.update_aliases(body={"actions": actions})
        for index in old_indices:
            if index != alias and self._es.indices.exists(index=index):
                self._es.indices.delete(index=index)
        logging.info("replaced index alias %s with %s", alias, physical)

    def search_index_name(self, index='*'):
        self._ensure_connected()
        idx = self._es.indices.get_alias(index=index)
        if len(idx) > 0:
            return pd.DataFrame(idx)[0].tolist()
        return []

    def delete_doc(self, index, field, value, query_type='exact'):
        self._ensure_connected()
        if self.has_index(index):
            query_dict = self._gen_query(index, query_type, field, value)
            query_dict.pop('size')
            logging.info(f"deleting in index: {index}, {query_dict}")
            self._es.delete_by_query(index=index, body=query_dict)
        else:
            logging.warning(f'cannot find index: {index}')

    def clone_index(self, index_name, clone_name):
        self._ensure_connected()
        logging.info(f'cloning {index_name} to {clone_name}')

        assert self.has_index(index_name) and not self.has_index(clone_name), \
            f'cannot clone {index_name} to {clone_name}'

        current_mapping = self._es.indices.get_mapping(index=index_name)
        properties = current_mapping[index_name]['mappings']['properties']

        # Modify the mapping to add keyword for all text fields
        for field, definition in properties.items():
            if definition['type'] == 'text':
                properties[field] = TEXT_WITH_KEYWORD_SCHEMA
            if definition['type'] == 'date':
                properties[field] = DATE_SCHEMA

        # Create a new temporary index with the updated mapping
        self._es.indices.create(index=clone_name, settings=LOWERCASE_NORMALIZER)
        self._es.indices.put_mapping(index=clone_name,
                                     body={'properties': properties})

        # Reindex data from old index to the new index
        self._es.reindex(body={
            "source": {"index": index_name},
            "dest": {"index": clone_name}
        }, wait_for_completion=True, request_timeout=TIMEOUT * 10)

    def rename_index(self, old_index_name, new_index_name):
        self._ensure_connected()
        logging.info(f'start rename {old_index_name} to {new_index_name}')

        assert self.has_index(old_index_name) and not self.has_index(new_index_name), \
            f'cannot rename {old_index_name} to {new_index_name}'

        temp_index_name = old_index_name + "_temp"

        self.clone_index(old_index_name, temp_index_name)

        self._es.indices.delete(index=old_index_name)

        # Now, reindex data from temporary index to the new index name
        self.clone_index(temp_index_name, new_index_name)

        # After verifying data, delete the temporary index
        self._es.indices.delete(index=temp_index_name)

    def update_index_fields(self, index_name):
        self._ensure_connected()
        logging.info(f'updating text fields in {index_name}')

        temp_index_name = index_name + "_temp"
        self.clone_index(index_name, temp_index_name)

        self._es.indices.delete(index=index_name)
        self.rename_index(temp_index_name, index_name)


default_es = ElasticSearchManager(app=None, lazy=True)

if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    names = default_es.search_index_name('analyst')
    [default_es.update_index_fields(x) for x in names if not x.startswith('.')]
