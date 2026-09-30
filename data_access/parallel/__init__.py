"""Plugin manifest for JobExecutor parallel backends.

`common.utils.parallel_registry` imports this package (by config convention) to discover
backends. Importing it triggers each backend module to self-register. This is the ONE
place backends are listed, and it lives in the data_access layer — `common` never names a
concrete backend. Add a backend = create its module (self-registering) and import it here.

Imports must stay light: backend modules import only the registry + stdlib at top and
defer heavy deps (ray, databricks SDK, cloudpickle) into call-time, so importing this
package is cheap and safe even where ray/sdk aren't installed.
"""
import logging

for _module in ('data_access.ray.parallel_backend', 'data_access.spark.parallel_backend'):
    try:
        __import__(_module)
    except Exception:
        logging.warning(f'parallel backend module not available: {_module}', exc_info=True)
