import os
import re
import shutil
from .dir_utils import (
    TempDirCopy, path_to_name, update_controller, SERPENT_EXT,
    strip_imports, strip_license, UploadContractsError
)
from .serpent_sig import mk_signature
from .compile_proc import CompileProc
from .rpc import HttpRpcClient, IpcRpcClient
from binascii import unhexlify, hexlify
import sha3
import rlp

STANDARD_EXTERNS = {
    'controller': 'extern controller: [lookup:[int256]:int256, assertIsWhitelisted:[int256]:int256]',
    # ERC20 and aliases used in Augur code
    'ERC20': 'extern ERC20: [allowance:[address,address]:uint256, approve:[address,uint256]:uint256, balanceOf:[address]:uint256, decimals:[]:uint256, name:[]:uint256, symbol:[]:uint256, totalSupply:[]:uint256, transfer:[address,uint256]:uint256, transferFrom:[address,address,uint256]:uint256]',
    'subcurrency': 'extern subcurrency: [allowance:[address,address]:uint256, approve:[address,uint256]:uint256, balanceOf:[address]:uint256, decimals:[]:uint256, name:[]:uint256, symbol:[]:uint256, totalSupply:[]:uint256, transfer:[address,uint256]:uint256, transferFrom:[address,address,uint256]:uint256]',
    'rateContract': 'extern rateContract: [rateFunction:[]:int256]',
    'forkResolveContract': 'extern forkResolveContract: [resolveFork:[int256]:int256]',
    'shareTokens': 'extern shareTokens: [allowance:[address,address]:int256, approve:[address,uint256]:int256, balanceOf:[address]:int256, changeTokens:[int256,int256]:int256, createTokens:[address,uint256]:int256, destroyTokens:[address,uint256]:int256, getDecimals:[]:int256, getName:[]:int256, getSymbol:[]:int256, modifySupply:[int256]:int256, setController:[address]:int256, suicideFunds:[address]:_, totalSupply:[]:int256, transfer:[address,uint256]:int256, transferFrom:[address,address,uint256]:int256]',
}
EXTERN = re.compile('^extern (?P<name>\w+): \[.+\]$')
CONTROLLER_V1 = re.compile('^(?P<indent>\s*)(?P<alias>\w+) = Controller.lookup\([\'"](?P<name>\w+)[\'"]\)')
CONTROLLER_V1_MACRO = re.compile('^macro Controller: (?P<address>0x[0-9a-fA-F]{1,40})$')
DEFAULT_CONTROLLER = '0xDEADBEEF'
VALID_ADDRESS = re.compile('^0x[0-9A-F]{1,40}$')


def imports_to_externs(source_dir, target_dir):
    """Translates code using import syntax to standard Serpent externs."""
    with TempDirCopy(source_dir) as td:
        serpent_paths = td.find_files(SERPENT_EXT)

        contracts = {}

        for path in serpent_paths:
            # TODO: Something besides hard coding this everywhere!!!
            if os.path.basename(path) == 'controller.se':
                continue

            name = path_to_name(path)

            with open(path) as f:
                code_lines = [line.rstrip() for line in f]

            try:
                code_lines = update_controller(code_lines, DEFAULT_CONTROLLER)
                license, dependencies, other_code = strip_imports(code_lines)
            except UploadContractsError as exc:
                raise UploadContractsError(
                    'Caught error while processing {name}: {error}',
                    name=name,
                    error=exc.message)

            info = {'license': license,
                    'dependencies': dependencies,
                    'stripped_code': '\n'.join(other_code)}

            # the stripped code is writen back so the serpent module can handle 'inset' properly
            with open(path, 'w') as f:
                f.write(info['stripped_code'])

            info['signature'] = mk_signature(path, name)
            info['path'] = path
            contracts[name] = info

        lookup_fmt = '{} = self.controller.lookup(\'{}\')'
        for name in contracts:
            info = contracts[name]
            signatures = ['', STANDARD_EXTERNS['controller'], '']
            for oname, alias in info['dependencies']:
                signatures.append('')
                signatures.append(lookup_fmt.format(alias, oname))
                signatures.append(contracts[oname]['signature'])
            signatures.append('') # blank line between signatures section and rest of code
            new_code = '\n'.join(info['license'] + signatures + [info['stripped_code']])
            path = info['path']

            with open(path, 'w') as f:
                f.write(new_code)

        td.commit(target_dir)


def update_externs(source_dir, controller):
    """Updates all externs in source_dir."""

    with TempDirCopy(source_dir) as td:
        extern_map = STANDARD_EXTERNS.copy()
        serpent_files = td.find_files(SERPENT_EXT)

        for path in serpent_files:
            # TODO: Something besides hard coding this everywhere!!!
            if os.path.basename(path) == 'controller.se':
                continue

            name = path_to_name(path)
            extern_map[name] = mk_signature(path, name)

        for path in serpent_files:
            # TODO: Something besides hard coding this everywhere!!!
            if os.path.basename(path) == 'controller.se':
                continue

            with open(path) as f:
                code_lines = [line.rstrip() for line in f]

            if controller:
                try:
                    code_lines = update_controller(code_lines, controller)
                except Exception as exc:
                    raise UploadContractsError(
                        'Caught error while processing {path}: {err}',
                        path=td.original_path(path),
                        err=str(exc))

            license, code_lines = strip_license(code_lines)

            for i in range(len(code_lines)):
                line = code_lines[i]
                m = EXTERN.match(line)

                if (line.startswith('extern') and m is None or
                    m and m.group(1) not in extern_map):

                    raise UploadContractsError(
                        'Weird extern at line {line_num} in file {path}: {line}',
                        line_num=(len(license) + i + 1),
                        path=td.original_path(path),
                        line=line)
                elif m:
                    extern_name = m.group(1)
                    code_lines[i]  = extern_map[extern_name]

            code_lines = '\n'.join(license + code_lines)
            with open(path, 'w') as f:
                f.write(code_lines)

        shutil.rmtree(source_dir)
        td.commit(source_dir)


def upgrade_controller(source, controller):
    """Replaces controller macros with an updateable storage value."""
    with TempDirCopy(source) as td:
        serpent_files = td.find_files(SERPENT_EXT)

        for path in serpent_files:
            # TODO: Something besides hard coding this everywhere!!!
            if os.path.basename(path) == 'controller.se':
                continue

            with open(path) as f:
                code_lines = [line.rstrip() for line in f]

            if controller:
                try:
                    code_lines = update_controller(code_lines, controller)
                except UploadContractsError as exc:
                    raise UploadContractsError('Caught error while processing {path}: {err}',
                                               path=td.original_path(path),
                                               err=exc.message)

            new_code = []
            for i in range(len(code_lines)):
                line = code_lines[i]
                if CONTROLLER_V1_MACRO.match(line):
                    continue # don't include the macro line in the new code
                m = CONTROLLER_V1.match(line)
                if m:
                    new_code.append('{indent}{alias} = self.controller.lookup(\'{name}\')'.format(**m.groupdict()))
                else:
                    new_code.append(line)

            with open(path, 'w') as f:
                f.write('\n'.join(new_code))

        shutil.rmtree(source)
        td.commit(source)


def address(creator, nonce):
    return sha3.keccak_256(rlp.encode([creator, nonce])).digest()[12:]


def upload_contracts(rpc_address, creator, controller, specials, source_dir):
    with TempDirCopy(source_dir) as td:
        if rpc_address.startswith('http'):
            client = HttpRpcClient(rpc_address)
        else:
            client = IpcRpcClient(rpc_address)

        creator_nonce = int(client.send('eth_getTransactionCount'), 16)
        controller_address = address(unhexlify())



