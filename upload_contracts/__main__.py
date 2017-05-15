from __future__ import print_function
import sys
import argparse
from .commands import imports_to_externs, update_externs, upgrade_controller, compile, UploadContractsError


def main():
    parser = argparse.ArgumentParser(
        description='Compiles collections of serpent contracts.',
        epilog='Try a command followed by -h to see it\'s help info.')

    commands = parser.add_subparsers(title='commands')

    translate = commands.add_parser('translate',
                                    help='Translate imports to externs.')
    translate.add_argument('-s', '--source',
                           help='Directory to search for Serpent code.',
                           required=True)
    translate.add_argument('-t', '--target',
                           help='Directory to save translated code in.',
                           required=True)
    translate.set_defaults(command='translate')

    update = commands.add_parser('update',
                                 help='Updates the externs in --source.')
    update.add_argument('-s', '--source',
                        help='Directory to search for Serpent code.',
                        required=True)
    update.add_argument('-c', '--controller',
                        help='The address in hex of the Controller contract.',
                        default=None)
    update.set_defaults(command='update')

    compile_ = commands.add_parser('compile',
                                   help='Compiles and uploads all the contracts in --source. (TODO)')
    compile_.add_argument('-s', '--source',
                          help='Directory to search for Serpent code.',
                          required=True)
    compile_.add_argument('-r', '--rpcaddr',
                          help='Address of RPC server.',
                          default=None)
    compile_.add_argument('-o', '--out',
                          help='Filename for address json.')
    compile_.add_argument('-O', '--overwrite',
                          help='If address json already exists, overwrite.',
                          action='store_true', default=False)
    compile_.add_argument('-C', '--creator',
                          help='Address used for contract creation',
                          default=None)
    compile_.add_argument('-c', '--controller',
                          help='filename of the controller contract',
                          required=True)
    compile_.add_argument('-s', '--specials',
                          help='comma separated list of filenames to compile first.',
                          default=None)
    compile_.set_defaults(command='compile')

    upgrade = commands.add_parser('upgrade', help='Upgrades to the new controller mechanism')
    upgrade.add_argument('-s', '--source', help='Directory to search for Serpent code.', required=True)
    upgrade.add_argument('-c', '--controller', help='Sets the controller address', default=None)
    upgrade.set_defaults(command='upgrade')

    args = parser.parse_args()

    try:
        if not hasattr(args, 'command'):
            parser.print_help()
        elif args.command == 'translate':
            imports_to_externs(args.source, args.target)
        elif args.command == 'update':
            update_externs(args.source, args.controller)
        elif args.command == 'upgrade':
            upgrade_controller(args.source, args.controller)
        else:
            raise UploadContractsError('command not implemented: {}', args.command)
    except UploadContractsError as exc:
        print(exc)
        return 1
    else:
        return 0

if __name__ == '__main__':
    sys.exit(main())
