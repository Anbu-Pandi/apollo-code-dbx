import json
import logging

import pyarrow.flight as flight

from dash_reports.cdx_research.jobs import ResearchJobs
from dash_reports.cdx_research.market import MarketOverview
from dash_reports.cdx_research.pipeline import run
from dash_reports.sub_servers.cdx_research_server.settings import APP, endpoint, setting, tls_files


class CDXResearchFlightServer(flight.FlightServerBase):
    def __init__(self, host=None, port=None, jobs=None, tls=None):
        host, port = endpoint(host, port)
        files = tls_files() if tls is None else tls
        if files:
            cert, key = files
            super().__init__(flight.Location.for_grpc_tls(host, port),
                             tls_certificates=[(cert.read_bytes(), key.read_bytes())])
        else:
            super().__init__(flight.Location.for_grpc_tcp(host, port))
        self.market = MarketOverview(refresh_seconds=float(setting('market.refresh_seconds', 30)))
        self.jobs = jobs or ResearchJobs(runner=lambda kind, params: run(kind, params, self.market),
                                         max_jobs=int(setting('jobs.max_jobs', 24)),
                                         workers=int(setting('jobs.workers', 2)))
        if jobs is None:
            self.market.snapshot()
        logging.info('CDX Research Flight listening on %s:%s', host, self.port)

    def list_actions(self, context):
        return [('health_check', 'Service health'), ('research_start', 'Start an input-keyed run'),
                ('research_poll', 'Get newly completed panels'), ('market_snapshot', 'Instrument catalogue and live price band')]

    def do_action(self, context, action):
        try:
            body = json.loads(action.body.to_pybytes() or b'{}')
            if action.type == 'health_check':
                result = {'success': True, 'service': APP}
            elif action.type == 'market_snapshot':
                result = self.market.snapshot()
            elif action.type == 'research_start':
                result = self.jobs.start(body['kind'], body.get('params'), body.get('force', False))
            elif action.type == 'research_poll':
                result = self.jobs.poll(body['id'], body.get('since', 0))
            else:
                raise ValueError(f'Unknown action: {action.type}')
        except Exception as exc:
            logging.exception('Research action failed: %s', action.type)
            result = {'success': False, 'error': str(exc)}
        yield flight.Result(json.dumps(result, allow_nan=False).encode('utf-8'))

    def shutdown(self):
        super().shutdown()
        self.jobs.close()
