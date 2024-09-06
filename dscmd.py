"""
Trigger the documentation server to generate the documentation for the given
targets, products, docsets, and languages.
"""
import argparse
from itertools import product
import os
import re
import sys

import json
import requests

__version__ = "0.2.0"
__author__ = "Tom Schraitle <toms@suse.de>"


def read_config():
    """Read configuration from the ~/.dscmd-server.conf file"""
    config_path = os.path.expanduser("~/.dscmd-server.conf")
    if not os.path.exists(config_path):
        print("Configuration file not found.")
        sys.exit(1)

    config = {}
    with open(config_path, 'r') as conf_file:
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
    parser = argparse.ArgumentParser(description=__doc__,
                                     epilog="Version %s written by %s " % (__version__, __author__),
                                     )
    parser.add_argument('--version',
                        action='version',
                        version='%(prog)s ' + __version__
                        )

    subparsers = parser.add_subparsers(help='sub-command help',
                                       dest='subcommand',
                                       )
    queue = subparsers.add_parser('queue',
                                  help=(
                                         'Check queue status of Docserv2 '
                                         'instance defined in '
                                         '`~/dscmd-server.conf')
                                  )
    queue.add_argument("--full",
                       action="store_true",
                       default=False,
                       help="Show full details of the queue"
                       )

    trigger = subparsers.add_parser('trigger',
                                    help=('Generate a JSON string '
                                          'and send it to a Docserv² '
                                          'instance')
                                    )
    trigger.add_argument("-t", "--targets",
                        required=True,
                        help="Target server names")
    trigger.add_argument("-p", "--products",
                        required=True,
                        help="Products to process")
    trigger.add_argument("-d", "--docsets",
                        required=True,
                        help="Docsets to process")
    trigger.add_argument("-l", "--langs",
                        required=True,
                        help="Languages to process")

    args = parser.parse_args()
    args.parser = parser
    if args.subcommand == 'trigger':
        args.func = trigger
        args.targets = args.targets.split(',')
        args.products = args.products.split(',')
        args.docsets = args.docsets.split(',')
        args.langs = args.langs.split(',')
    elif args.subcommand == 'queue':
        args.func = queue
    return args


def queue(args, config):
    """Check the queue status of the Docserv² instance"""
    print("Queue status of Docserv² instance:")
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
        print(f"Failed to fetch data. Status code: {response.status_code}", file=sys.stderr)
        return response.status_code


def trigger(args, config):
    """Trigger the Docserv² instance to generate the documentation"""

    for target in args.targets:
        if target not in config['validtargets']:
            print(f"Error: Invalid target '{target}'. "
                  f"Must be one of {config['validtargets']}."
                  )
            sys.exit(1)

    # Prepare the JSON payload
    payload = [
        {
            "target": t, "product": p, "docset": d, "lang": l
        }
        for t, p, d, l in product(args.targets, args.products, args.docsets, args.langs)
    ]

    headers = {
        "Content-Type": "application/json; charset=utf-8"
    }

    # Server details from config
    server_url = f"{config['server']}:{config['port']}"
    if not server_url.startswith("http"):
        server_url = f"http://{server_url}"

    # Sending data to server
    try:
        print("Sending payload %r to server %s..." % (payload, server_url))
        response = requests.post(server_url,
                                 json=payload,
                                 headers=headers
                                 )
        if response.status_code == 200:
            print("Data sent successfully.")
        else:
            print(f"Failed to send data. "
                  f"Server responded with status code {response.status_code}",
                  file=sys.stderr)
            return response.status_code
        return 0
    except requests.RequestException as e:
        print(f"Error occurred while sending data: {str(e)}", file=sys.stderr)
        return 1


def main(cliargs=None):
    """Main function to process the arguments and send data"""
    args = parsecli(cliargs)
    print(">>> args:", args)

    config = read_config()
    print(">>> config:", config)

    def help(args, config):
        return args.parser.print_help()

    func = {
        'queue': queue,
        'trigger': trigger
    }.get(args.subcommand, help)

    return func(args, config)


if __name__ == "__main__":
    sys.exit(main())
