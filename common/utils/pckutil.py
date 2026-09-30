from importlib.metadata import distribution, PackageNotFoundError
import os
from datetime import datetime


def get_package_details(package_name):
    try:
        # Get the distribution object for the package
        dist = distribution(package_name)
    except PackageNotFoundError:
        return f"Package '{package_name}' is not installed."

    details = {
        'Version': dist.version,
        'Location': str(dist._path),  # This accesses the private path attribute; use carefully as it may change
        'Requires': list(dist.requires or [])  # Returns a list of requirements
    }

    # Attempt to find the installation date by checking the metadata directory's modification time
    dist_info = dist._path.parent
    if dist_info.exists():
        details['Metadata Directory'] = str(dist_info)
        timestamp = os.path.getmtime(dist_info)
        details['Installed On'] = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')

    return details


if __name__ == '__main__':
    import logging
    import json
    from common.utils.logutil import setuplogger

    setuplogger()
    # Example usage:
    package_name = 'numpy'  # Replace 'numpy' with the package you are interested in
    logging.info(json.dumps(get_package_details(package_name), indent=4))
