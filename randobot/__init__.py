import sys

sys.path.append('/opt/py')

import argparse
import logging

from .bot import RandoBot


def main():
    parser = argparse.ArgumentParser(
        description='RandoBot, because OoTR seeds weren\'t scary enough already.',
    )
    parser.add_argument('category_slug', type=str, help='racetime.gg category')
    parser.add_argument('client_id', type=str, help='racetime.gg client ID')
    parser.add_argument('client_secret_path', type=str, help='path to file containing racetime.gg client secret')
    parser.add_argument('--rsl_script_path', default='/opt/git/github.com/matthewkirby/plando-random-settings/master', help='use the RSL script at this path')
    parser.add_argument('--output_path', default='/var/www/ootr.fenhl.net/seed', help='save patch files to this path')
    parser.add_argument('--base_uri', default='https://ootr.fenhl.net/seed/', help='add the patch filename to this prefix to generate the link')
    parser.add_argument('--verbose', '-v', action='store_true', help='verbose output')
    parser.add_argument('--host', type=str, nargs='?', help='change the ractime.gg host (debug only!')
    parser.add_argument('--insecure', action='store_true', help='don\'t use HTTPS (debug only!)')

    args = parser.parse_args()

    logger = logging.getLogger()
    handler = logging.StreamHandler(sys.stdout)

    if args.verbose:
        logger.setLevel(logging.DEBUG)
        handler.setLevel(logging.DEBUG)

    handler.setFormatter(logging.Formatter(
        '[%(asctime)s] %(name)s (%(levelname)s) :: %(message)s'
    ))
    logger.addHandler(handler)

    if args.host:
        RandoBot.racetime_host = args.host
    if args.insecure:
        RandoBot.racetime_secure = False

    with open(args.client_secret_path) as client_secret_f:
        client_secret = client_secret_f.read().strip()

    inst = RandoBot(
        rsl_script_path=args.rsl_script_path,
        output_path=args.output_path,
        base_uri=args.base_uri,
        category_slug=args.category_slug,
        client_id=args.client_id,
        client_secret=client_secret,
        logger=logger,
    )
    inst.run()


if __name__ == '__main__':
    main()
