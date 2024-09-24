#!/usr/bin/env python3.11

"""
Trigger the documentation server to generate the documentation for the given
targets, products, docsets, and languages.
"""
import argparse
from itertools import product
import logging
from logging.config import dictConfig
import os
import re
import sys

import json
import requests

__version__ = "0.2.0"
__author__ = "Tom Schraitle <toms@suse.de>"


PYTHON_VERSION: str = f"{sys.version_info.major}.{sys.version_info.minor}"

CONFIG_FILE = os.path.expanduser("~/.dscmd-server.conf")

SEPARATOR = re.compile(r"[,; ]")


# --- Loggers
LOGGERNAME = "dscmd"
#: The log file to use
LOGFILE = "/tmp/dscmd.log"
#: Map verbosity level (int) to log level
LOGLEVELS = {
    None: logging.WARNING,  # fallback
    0: logging.ERROR,
    1: logging.WARNING,
    2: logging.INFO,
    3: logging.DEBUG,
}

#: The dictionary, passed to :class:`logging.config.dictConfig`,
#: is used to setup your logging formatters, handlers, and loggers
#: For details, see https://docs.python.org/3.4/library/logging.config.html#configuration-dictionary-schema
DEFAULT_LOGGING_DICT = {
    "version": 1,
    "disable_existing_loggers": True,
    "formatters": {
        "standard": {"format": "[%(levelname)s] %(funcName)s: %(message)s"},
    },
    "handlers": {
        "console": {
            "level": "NOTSET",  # will be set later
            "formatter": "standard",
            "class": "logging.StreamHandler",
        },
        "file": {
            "level": "DEBUG",  # we want all in the log file
            "formatter": "standard",
            "class": "logging.FileHandler",
            "filename": LOGFILE,
            "mode": "w",
        },
    },
    "loggers": {
        LOGGERNAME: {
            "handlers": ["console",],
            "level": "DEBUG",
            # 'propagate': True
        },
        "": {
            "level": "NOTSET",
        },
    },
}

#
# Change root logger level from WARNING (default) to NOTSET
# in order for all messages to be delegated.
logging.getLogger().setLevel(logging.NOTSET)

log = logging.getLogger(LOGGERNAME)



def read_config():
    """Read configuration from the ~/.dscmd-server.conf file"""

    if not os.path.exists(CONFIG_FILE):
        print("Configuration file not found.")
        sys.exit(1)

    config = {}
    with open(CONFIG_FILE, 'r') as conf_file:
        for line in conf_file:
            if line.startswith('#') or not line.strip():
                continue
            if '=' in line:
                key, value = line.strip().split('=', 1)
                config[key] = value
    return config


def parsecli(cliargs=None) -> argparse.Namespace:
    """Parse CLI with :class:`argparse.ArgumentParser` and return parsed result
    :param cliargs: Arguments to parse or None (=use sys.argv)
    :return: parsed CLI result
    """
    config = read_config()

    description = (
        f"Using the configuration settings:\n"
        f" * Configuration file: {CONFIG_FILE!r}\n"
        f" * Server: {config.get('server', 'localhost')}\n"
        f" * Port: {config.get('port', 8080)}\n"
        f" * Valid targets: {config.get('validtargets', 'None')}\n"
    )
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Version %s written by %s " % (__version__, __author__),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--version',
                        action='version',
                        version='%(prog)s ' + __version__
                        )
    parser.add_argument("-v", "--verbose",
        action="count",
        default=0,
        help="increase verbosity level",
    )

    # Common arguments for trigger and metadata
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument("-t", "--targets",
                      help="Target server names")
    common_parser.add_argument("-p", "--products",
                        help="Products to process")
    common_parser.add_argument("-d", "--docsets",
                        help="Docsets to process")
    common_parser.add_argument("-l", "--langs",
                        help="Languages to process"
                        )

    subparsers = parser.add_subparsers(help='sub-command help',
                                       dest='subcommand',
                                       required=True,
                                       )
    queue = subparsers.add_parser(
        'queue',
        aliases=['q'],
        help=(
              'Check queue status of Docserv2 '
              f'instance defined in {CONFIG_FILE!r}'
              ),
        epilog=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,

        )
    queue.add_argument("--full",
                       action="store_true",
                       default=False,
                       help="Show full details of the queue"
                       )
    queue.set_defaults(func=queue)

    meta = subparsers.add_parser(
        "meta",
        parents=[common_parser],
        aliases=["m", "metadata"],
        help="Trigger metadata rebuild of the Docserv2 instance",
        epilog=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        )
    meta.set_defaults(func=metadata)
    meta.add_argument("--debug",
                      action="store_true",
                      default=False,
                      help="Draft mode, doesn't execute the command")


    trg = subparsers.add_parser(
        'trigger',
        parents=[common_parser],
        aliases=['t'],
        help=('Generate a JSON string '
              'and send it to a Docserv² instance'
              ),
        epilog=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    trg.set_defaults(func=trigger)

    args = parser.parse_args()
    args.parser = parser
    # Setup logging and the log level according to the "-v" option
    loglevel = LOGLEVELS.get(args.verbose, logging.DEBUG)
    DEFAULT_LOGGING_DICT["handlers"]["console"]["level"] = loglevel
    DEFAULT_LOGGING_DICT["loggers"][LOGGERNAME]["level"] = loglevel
    dictConfig(DEFAULT_LOGGING_DICT)

    args.config = config
    log.debug(">>> config: %r", args.config)

    if args.subcommand in ('trigger', 't', 'meta', 'metadata', 'm'):
        args.targets = [] if args.targets is None else SEPARATOR.split(args.targets)
        args.products = [] if args.products is None else SEPARATOR.split(args.products)
        args.docsets = [] if args.docsets is None else SEPARATOR.split(args.docsets)
        args.langs = [] if args.langs is None else SEPARATOR.split(args.langs)

    return args


def queue(args: argparse.Namespace) -> int:
    """Check the queue status of the Docserv² instance"""
    config = args.config
    log.info("Queue status of Docserv² instance:")
    # wget -qO - "${server}:${port}" | jq '.[] | .id,.product,.docset,.lang,.open,.building' | sed -r 's/^"[a-f0-9]{9}"$/---/'
    server_url = f"{config['server']}:{config['port']}"
    if not server_url.startswith("http"):
        server_url = f"http://{server_url}"

    # Fetch data from the server
    response = requests.get(server_url)
    if response.status_code == 200:
        data = response.json()  # Parse JSON response
        if args.full:
            print(json.dumps(data, indent=2))
            return 0

        # Process and print the desired fields
        for item in data:
            for key in ['id', 'product', 'docset', 'lang', 'open', 'building']:
                value = item.get(key, "")

                # Apply regex transformation if needed (replace hex strings with "---")
                if re.match(r'^"[a-f0-9]{9}"$', f'"{value}"'):
                    value = "---"

                print(value)
        return 0
    else:
        log.error("Failed to fetch data. Status code: %s: %s",
                  (response.status_code, response.text)
                  )
        return response.status_code


def post2server(server_url: str, payload: list, headers:None|dict = None) -> requests.Response:
    """Send data to the server"""
    if headers is None:
        headers = {
            "Content-Type": "application/json; charset=utf-8"
        }
    # Sending data to server
    log.info("Sending payload %r to server %s..." % (payload, server_url))
    response = requests.post(
        server_url,
        json=payload,
        headers=headers
    )
    if response.status_code == 200:
        log.info(f"Data sent successfully.\nResponse: {response.text}")
    else:
        log.fatal(
            "Failed to send data. Server responded with status code %s",
            response.status_code)
    return response


def trigger(args: argparse.Namespace) -> int|None:
    """Trigger the Docserv² instance to generate the documentation"""
    config = args.config
    for target in args.targets:
        if target not in config['validtargets']:
            log.fatal(
                "Error: Invalid target %r. "
                "Must be one of %s.",
                (target, config['validtargets'])
            )
            return 20

    # Prepare the JSON payload
    payload = [
        {
            "target": t,
            "product": p,
            "docset": d,
            "lang": l,
        }
        for t, p, d, l in product(args.targets, args.products, args.docsets, args.langs)
    ]

    # Server details from config
    server_url = f"{config['server']}:{config['port']}"
    if not server_url.startswith("http"):
        server_url = f"http://{server_url}"

    # Sending data to server
    try:
       post2server(server_url, payload)
       log.info("Data sent successfully.")
       return 0
    except requests.RequestException as e:
        raise ValueError("Error occurred while sending data: ")


def metadata(args: argparse.Namespace) -> int:
    """
    Trigger metadata rebuild of the Docserv² instance
    """
    config = args.config
    # Server details from config
    server_url = f"{config['server']}:{config['port']}"
    if not server_url.startswith("http"):
        server_url = f"http://{server_url}/metadata"

    if not args.targets:
        args.parser.error("metadata: You must define at least one target.")

    if not args.products and args.docsets:
        args.parser.error("metadata: You can't use docsets without a product.")

    if not args.langs:
        args.langs = ['en-us']

    payload = [
        {
            "target": t, "product": p, "docset": d, "lang": l
        }
        for t, p, d, l in product(args.targets, args.products, args.docsets, args.langs)
    ]

    if args.debug:
        print("Trigger metadata with the following parameters:")
        print("Targets:", args.targets)
        print("Products:", args.products)
        print("Docsets:", args.docsets)
        print("Languages:", args.langs)
        print("Payload:", json.dumps(payload, indent=2))
        print("Draft mode. Not sending data to the server.")
        return 0

    # Sending data to server
    try:
        response = post2server(server_url, payload)
        if response.status_code == 200:
            return 0
        else:
            log.fatal(
                "Failed to send data. "
                "Server responded with status code %s",
                response.status_code)
            log.fatal("Response: %s", response.text)
            return response.status_code
    except requests.RequestException as e:
        log.fatal("Error occurred while sending data: %s: %s", str(e))
        return 1


def main(cliargs=None):
    """Main function to process the arguments and send data"""
    args = parsecli(cliargs)
    log.debug("Parsed arguments: %s", args)

    try:
        log.debug(f"Trying to call {args.subcommand}...")
        if args.subcommand:
            args.func(args)

    except ValueError as e:
        log.error("%s", str(e))
        return 100


if __name__ == "__main__":
    sys.exit(main())
