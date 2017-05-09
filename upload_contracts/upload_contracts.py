#!/usr/bin/env python
# Copyright (c) 2017 Christian Calderon

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
from __future__ import print_function
from binascii import hexlify, unhexlify
import argparse
import os
import errno
import re
import shutil
import sys
import tempfile
import warnings
import ethereum.tester
import ethereum.transactions
import ethereum.processblock
import ethereum.abi
import socket
import select
import json
import serpent

IPC_SOCK = None

IMPORT = re.compile('^import (?P<name>\w+) as (?P<alias>\w+)$')
EXTERN = re.compile('^extern (?P<name>\w+): \[.+\]$')
CONTROLLER_V1 = re.compile('^(?P<indent>\s*)(?P<alias>\w+) = Controller.lookup\([\'"](?P<name>\w+)[\'"]\)')
CONTROLLER_V1_MACRO = re.compile('^macro Controller: (?P<address>0x[0-9a-fA-F]{1,40})$')
CONTROLLER_INIT = re.compile('^(?P<indent>\s*)self.controller = 0x[0-9A-F]{1,40}')
INDENT = re.compile('^$|^[#\s].*$')
FUNCTION = re.compile('^def (.+)\((.*)\):$', re.M)
RETURN = re.compile('^\s*[^#].+return\((.*)\).*$', re.M)
IGNORE = ('any', 'init', 'shared')
ARGSEP = re.compile(',\s?')
TYPE_INFO = re.compile(
    '^(u?int|u?fixed|address|bool|bytes|string|function)'
    '(\d+x\d+|\d+)?'
    '(\[\d*\])?$')

STANDARD_EXTERNS = {
    'controller': 'extern controller: [lookup:[int256]:int256, assertIsWhitelisted:[int256]:int256]',
    # ERC20 and aliases used in Augur code
    'ERC20': 'extern ERC20: [allowance:[address,address]:uint256, approve:[address,uint256]:uint256, balanceOf:[address]:uint256, decimals:[]:uint256, name:[]:uint256, symbol:[]:uint256, totalSupply:[]:uint256, transfer:[address,uint256]:uint256, transferFrom:[address,address,uint256]:uint256]',
    'subcurrency': 'extern subcurrency: [allowance:[address,address]:uint256, approve:[address,uint256]:uint256, balanceOf:[address]:uint256, decimals:[]:uint256, name:[]:uint256, symbol:[]:uint256, totalSupply:[]:uint256, transfer:[address,uint256]:uint256, transferFrom:[address,address,uint256]:uint256]',
    'rateContract': 'extern rateContract: [rateFunction:[]:int256]',
    'forkResolveContract': 'extern forkResolveContract: [resolveFork:[int256]:int256]',
    'shareTokens': 'extern shareTokens: [allowance:[address,address]:int256, approve:[address,uint256]:int256, balanceOf:[address]:int256, changeTokens:[int256,int256]:int256, createTokens:[address,uint256]:int256, destroyTokens:[address,uint256]:int256, getDecimals:[]:int256, getName:[]:int256, getSymbol:[]:int256, modifySupply:[int256]:int256, setController:[address]:int256, suicideFunds:[address]:_, totalSupply:[]:int256, transfer:[address,uint256]:int256, transferFrom:[address,address,uint256]:int256]',
}

DEFAULT_RPCADDR = 'http://localhost:8545'
DEFAULT_CONTROLLER = '0xDEADBEEF'
VALID_ADDRESS = re.compile('^0x[0-9A-F]{1,40}$')

SERPENT_EXT = '.se'
MACRO_EXT = '.sem'

RAND_SEED = 3**160  # the seed ethereum.tester uses for it's rand function


class LoadContractsError(Exception):
    """Error class for all errors while processing Serpent code."""
    def __init__(self, msg, *format_args, **format_params):
        super(self.__class__, self).__init__(msg.format(*format_args, **format_params))
        if not hasattr(self, 'message'):
            self.message = self.args[0]


class TempDirCopy(object):
    """Makes a temporary copy of a directory and provides a context manager for automatic cleanup."""
    def __init__(self, source_dir):
        self.source_dir = os.path.abspath(source_dir)
        self.temp_dir = tempfile.mkdtemp()
        self.temp_source_dir = os.path.join(self.temp_dir,
                                            os.path.basename(self.source_dir))
        shutil.copytree(self.source_dir, self.temp_source_dir)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        shutil.rmtree(self.temp_dir)
        if not any((exc_type, exc, traceback)):
            return True
        return False

    def find_files(self, extension):
        """Finds all the files ending with the extension in the directory."""
        paths = []
        for directory, subdirs, files in os.walk(self.temp_source_dir):
            for filename in files:
                if filename.endswith(extension):
                    paths.append(os.path.join(directory, filename))

        return paths

    def commit(self, dest_dir):
        """Copies the contents of the temporary directory to the destination."""
        if os.path.exists(dest_dir):
            LoadContractsError(
                'The target path already exists: {}'.format(
                    os.path.abspath(dest_dir)))

        shutil.copytree(self.temp_source_dir, dest_dir)

    def cleanup(self):
        """Deletes the temporary directory."""
        try:
            shutil.rmtree(self.temp_dir)
        except OSError as exc:
            # If ENOENT it raised then the directory was already removed.
            # This error is not harmful so it shouldn't be raised.
            # Anything else is probably bad.
            if exc.errno == errno.ENOENT:
                return False
            else:
                raise
        else:
            return True

    def original_path(self, path):
        """Returns the orignal path which the copy points to."""
        return path.replace(self.temp_source_dir, self.source_dir)


def argtype(arg):
    """Get the ABI type of a function argument."""
    
    # The default type in serpent is int256
    if arg == '':
        return ''
    
    if ':' not in arg:
        return 'int256'

    assert arg.count(':') == 1, 'Too many colons in arg!'

    given_type = arg.split(':')[1].lstrip() # remove whitespace from e.g. 'x: int256'

    # These are serpent sugar types
    if given_type == 'arr':
        return 'int256[]'
    if given_type == 'str':
        return 'bytes'

    type_info = TYPE_INFO.match(given_type)

    assert type_info, 'Bad type! {}'.format(given_type)

    base, size, array = type_info.groups()

    if base in ('address', 'bool', 'function', 'string'):
        assert size is None
        return given_type

    if base == 'bytes':
        if size:
            assert 0 < int(size) <= 32, 'Bad size for fixed bytes type: {}'.format(given_type)
        return given_type

    if base in ('int', 'uint'):
        if size:
            assert 0 < int(size) <= 256, 'int size too large: {}'.format(given_type)
            assert int(size)%8 == 0, 'int size not a multiple of 8: {}'.format(given_type)
        else:
            size = '256'

    if base in ('ufixed', 'fixed'):
        if size:
            errmsg = 'fixedpoint type has improper size! {}'.format(given_type)
            assert 'x' in size, errmsg
            m, n = map(int, size.split('x'))
            assert m%8==0, errmsg
            assert n%8==0, errmsg
            assert 0 < m+n <= 256, errmsg
        else:
            size = '128x128'

    if array is None:
            array = ''

    return base + size + array


def mk_signature(code):
    """Experimental replacement for serpent.mk_signature.

    Note: This function assumes that insets never introduce
    new functions to the code, and also that macros don't 
    introduce new return types to code. This means that this
    function may be unable to determine if a single return-type
    exists, though this isn't used by the ABI and the default
    return type for this situation, '_', is used.
    """
    path = 'main'

    # replicate serpent.mk_signature behavior: paths and strings of code are accepted.
    if os.path.isfile(code):
        path = code
        with open(path) as f:
            code = f.read()

    func_matches = list(FUNCTION.finditer(code))
    funcs = []
    l = len(func_matches)
    for i, match in enumerate(func_matches):
        name, args = match.groups()
        if name not in IGNORE:
            if i < l - 1:
                returns = list(RETURN.finditer(code, match.start(), func_matches[i + 1].start()))
            else:
                returns = list(RETURN.finditer(code, match.start()))

            return_type = '_'
            if len(returns) > 0:
                types = [argtype(m.group(1)) for m in returns]
                if all(t == types[0] for t in types):
                    return_type = types[0]

            cannonical_types = ','.join(map(argtype, ARGSEP.split(args)))
            funcs.append(name + ':[' + cannonical_types + ']:' + return_type)
    return 'extern {}: [{}]'.format(path, ', '.join(funcs))


def pretty_signature(code, name):
    """Makes a Serpent style signature for the code, starting with 'extern name'."""
    ugly_signature = mk_signature(code)
    extern_name = 'extern {}'.format(name)
    name_end = ugly_signature.find(':')
    signature = extern_name + ugly_signature[name_end:]
    return signature


def strip_license(code_lines):
    """Separates the top-of-the-file license stuff from the rest of the code."""
    for i, line in enumerate(code_lines):
        if not line.startswith('#'):
            return code_lines[:i], code_lines[i:]
    return [], code_lines


def strip_imports(code_lines):
    """Separates dependency information from Serpent code in the file."""
    license, code = strip_license(code_lines)
    dependencies = []
    other_code = []

    for i, line in enumerate(code):
        if line.startswith('import'):
            m = IMPORT.match(line)
            if m:
                dependencies.append((m.group(1), m.group(2)))
            else:
                raise LoadContractsError(
                    'Weird import at {line_num}: {line}',
                    line_num=i,
                    line=line)
        else:
            other_code.append(line)

    return license, dependencies, other_code


def path_to_name(path):
    """Extracts the contract name from the path."""
    return os.path.basename(path).replace(SERPENT_EXT, '')


def update_controller(code_lines, controller_addr):
    """Updates the controller address in the code.

    If there is no 'data controller' declaration it is added.
    Similarly, if no controller initialization line is found in
    the init function, then it is added, and if there is no init
    function, one is added.
    """
    code_lines = code_lines[:]

    first_def = None
    data_line = None
    init_def = None
    for i, line in enumerate(code_lines):
        if line == 'data controller':
            data_line = i
        if line.startswith('def init()'):
            init_def = i
        if line.startswith('def') and first_def is None:
            first_def = i

    if first_def is None:
        raise LoadContractsError('No functions found! Is this a macro file?')

    controller_init = '    self.controller = {}'.format(controller_addr)

    if init_def is None:
        # If there's no init function, add it before the first function
        init_def = first_def
        code_lines = code_lines[:init_def] + ['def init():', controller_init, ''] + code_lines[init_def:]
    else:
        # If there is, add the controller init line to the top of the init function
        # and remove any other lines in init that set the value of self.controller
        code_lines.insert(init_def + 1, controller_init)
        i = init_def + 2
        while INDENT.match(code_lines[i]):
            m = CONTROLLER_INIT.match(code_lines[i])
            if m:
                del code_lines[i]
            else:
                i += 1

    if data_line is None:
        # If there's no 'data controller' line, add it before the init.
        code_lines = code_lines[:init_def] + ['data controller', ''] + code_lines[init_def:]

    return code_lines


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
            except LoadContractsError as exc:
                raise LoadContractsError(
                    'Caught error while processing {name}: {error}',
                    name=name,
                    error=exc.message)

            info = {'license': license,
                    'dependencies': dependencies,
                    'stripped_code': '\n'.join(other_code)}

            # the stripped code is writen back so the serpent module can handle 'inset' properly
            with open(path, 'w') as f:
                f.write(info['stripped_code'])

            info['signature'] = pretty_signature(path, name)
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
            extern_map[name] = pretty_signature(path, name)

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
                    raise LoadContractsError(
                        'Caught error while processing {path}: {err}',
                        path=td.original_path(path),
                        err=str(exc))

            license, code_lines = strip_license(code_lines)

            for i in range(len(code_lines)):
                line = code_lines[i]
                m = EXTERN.match(line)

                if (line.startswith('extern') and m is None or
                    m and m.group(1) not in extern_map):

                    raise LoadContractsError(
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
                except LoadContractsError as exc:
                    raise LoadContractsError('Caught error while processing {path}: {err}',
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


class ContractFunc(object):
    """An internal class used to make ABI contract functions."""

    def __init__(self, state, name, address, translator):
        self.translator = translator
        self.state = state
        self.name = name
        self.address = address

    def __call__(self, *args, **kwds):
        encoded_call = self.translator.encode_function_call(self.name, args)
        
        sender = kwds.get('sender', ethereum.tester.DEFAULT_KEY)
        value = kwds.get('value', 0)
        profiling = kwds.get('profiling', 0)
        output = kwds.get('output', '')

        result = self.state._send(
            sender,
            self.address,
            encoded_call,
            profiling=profiling)

        outdata = result.get('output', None)

        if outdata and output != 'raw':
            outdata = self.translator.decode_function_result(self.name, outdata)
            if len(outdata) == 1:
                outdata = outdata[0]

        if profiling:
            result['output'] = outdata
            return result

        return outdata

    def __str__(self):
        return '<function {} at address {}>'.format(self.name, hexlify(self.address).decode())


class Contract(object):
    """A contract class similar to ethereum.tester.ABIContract that is nicer with multiprocessing.

    Example:
    code = serpent.compile(stuff)
    full_sig = serpent.mk_full_signature(stff)
    contract = Contract(state, code, fullsig)
    """

    def __init__(self, state, compiled_code, full_sig, address):
        self._translator = ethereum.abi.ContractTranslator(full_sig)
        self._compiled_code = compiled_code
        self._full_sig = full_sig
        self._functions = {}

        for item in full_sig:
            if item['type'] == 'function':
                name = item['name']
                self._functions[name] = ContractFunc(state, name, address, self._translator)


class ContractLoader(object):
    """A class which updates and compiles Serpent code via ethereum.tester.state.

    Examples:
    # Using load_from_source
    contracts = ContractLoader()
    contracts.load_from_source('src', 'controller.se', ['mutex.se', 'cash.se', 'repContract.se'])
    # or using load_from_save
    contract.load_from_save('save.json')
    print(contracts.foo.echo('lol'))
    print(contracts['bar'].bar())
    contracts.cleanup()
    contracts.save('new_save.json')
    """

    def __init__(self):
        self._state = None
        self._interfaces = {}
        self._cleanups = []
        self._tester = ethereum.tester
        self._tester.gas_limit = 41000000
        self._contracts = {}
        self._paths = {}
        self._temp_dir = None

    def __getattr__(self, name):
        """Use it like a namedtuple!"""
        try:
            return self._contracts[name]
        except KeyError:
            raise AttributeError()

    def __getitem__(self, name):
        """Use it like a dict!"""
        try:
            return self._contracts[name]
        except KeyError:
            raise LoadContractsError('{!r} is not a contract!', name)

    def _compile(self, path):
        name = path_to_name(path)
        contract = self._contracts[name] = self._state.abi_contract(path)
        self._interfaces[name] = serpent.mk_full_signature(path)
        self._paths[name] = path
        self.controller.setValue(name.ljust(32, '\x00'), contract.address)
        self.controller.addToWhitelist(contract.address)

    def compile(self, contract):
        """Compiles the contract and creates a new ABIContract for it.

        The argument may be either the name of a contract already compiled,
        or it can be a path. If the argument refers to a contract that is already
        compiled, the copy of it's source is updated in the temporary directory,
        and the old contract is replaced by the new one. If the contract is new,
        it's code is copied to the temporary directory and a new contract is created.
        """
        temp_dir = self._temp_dir

        if contract in self._paths:
            # this is the name of a contract
            path_in_temp_dir = self._paths[contract]
            source_path = path_in_temp_dir.replace(temp_dir.temp_source_dir, temp_dir.source_dir, 1)

        if os.path.isfile(path):
            source_path = os.path.abspath(path)
            assert path.startswith(temp_dir.source_dir), 'path not in the source directory!'
            path_in_temp_dir = path.replace(temp_dir.source_dir, temp_dir.temp_source_dir, 1)
        else:
            raise LoadContractsError('The contract is not a known contract, or it\'s path is outside the source directory!')

        if os.path.isfile(path_in_temp_dir):
            os.remove(path_in_temp_dir)
            
        shutil.copy2(source_path, path_in_temp_dir)
        update_externs(temp_dir.temp_source_dir, '0x' + hexlify(self.controller.address).decode())
        self._compile(path_in_temp_dir)

    def save(self, path):
        """Saves state and contract info to a path.
        
        Note: If the path already exists, it gets overwritten.
        Also be aware that if you haven't called state.mine(),
        any pending transactions have not been committed to the
        state trie and so they won't be included in the save.
        """
        path = os.path.abspath(path)

        save = {'blocks': [], 'contracts': {}}

        # the last block in the state.blocks list has not been committed, so it's skipped.
        for block in self._state.blocks[:-1]:
            save['blocks'].append(hexlify(rlp.encode(block)).decode())

        for name in self._contracts:
            contract = self._contracts[name]
            save['contracts'][name] = {
                'address': hexlify(contract.address).decode(),
                'interface': self._interfaces[name],
                }

        save['source'] = os.path.abspath(self._temp_dir.source_dir)

        with open(path, 'w') as f:
            json.dump(save, f, sort_keys=True, indent=4)

    def load_from_save(self, save_path):
        """Loads contracts from a saved state."""
        with open(save_path) as f:
            save = json.load(f)

        self._temp_dir = TempDirCopy(save['source'])
        self._tester.rand.seed = RAND_SEED
        state = self._state = self._tester.state()

        for i, block in enumerate(save['blocks']):
            raw_block = rlp.decode(unhexlify(block))
            # At the low level, a block is a list containing three elements.
            # the first element is the block header, which is itself a list of stuff.
            # the second element is a list of transactions.
            for j, raw_tx in enumerate(raw_block[1]):
                tx = rlp.decode(rlp.encode(raw_tx), ethereum.transactions.Transaction)
                success, output = ethereum.processblock.apply_transaction(state.block, tx)
                if not success:
                    raise LoadContractsError('Tx #{} in block {} is invalid!', j, i)
            state.mine()

        for name in save['contracts']:
            address = save['contracts'][name]['address']
            interface = save['contracts'][name]['interface']
            raw_address = unhexlify(address)
            self._contracts[name] = ethereum.tester.ABIContract(state, interface, raw_address)
            self._interfaces[name] = interface

    def load_from_source(self, source_dir, controller, specials):
        """Loads contracts from a source directory with a new state.

        Note: The controller contract is called "controller" without regards to its filename,
        but all the other contracts are referenced by their filename minus the serpent extension.
        The latter may affect how you reference the contract in test code; A contract called buy&sell.se
        will only be accessible via contractLoader['buy&sell'], whereas a python friendly name like
        buyAndSell.se will allow you to do contractLoader.buyAndSell.
        """
        self._temp_dir = TempDirCopy(source_dir)
        self._cleanups.append(self._temp_dir.cleanup)
        self._tester.rand.seed = RAND_SEED
        self._state = self._tester.state()

        serpent_paths = self._temp_dir.find_files(SERPENT_EXT)

        for i in range(len(serpent_paths)):
            path = serpent_paths[i]
            if os.path.basename(path) == controller:
                print('Compiling controller')
                self._interfaces['controller'] = serpent.mk_full_signature(path)
                self._paths['controller'] = path
                self._contracts['controller'] = self._state.abi_contract(path)
                controller_addr = '0x' + hexlify(self.controller.address).decode()
                print('Updating externs')
                update_externs(self._temp_dir.temp_source_dir, controller_addr)
                del serpent_paths[i] # make sure we don't recompile this later
                break
        else:
            raise LoadContractsError('Unable to find controller: {}', controller)

        print('Compiling specials')
        for filename in specials:
            for i in range(len(serpent_paths)):
                path = serpent_paths[i]
                if os.path.basename(path) == filename:
                    print('    Compiling', filename)
                    self._compile(path)
                    del serpent_paths[i]
                    break
            else:
                raise LoadContractsError('Special contract not found: {}', filename)

        print('Compiling contracts')
        for path in serpent_paths:
            print('    Compiling', path)
            try:
                self._compile(path)
            except:
                print('Error compiling {}'.format(path))
                print()
                with open(path) as f:
                    for i, line in enumerate(f):
                        print(i + 1, line, sep=':', end='')
                print()
                raise


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
                          default=DEFAULT_RPCADDR)
    compile_.add_argument('-o', '--out',
                          help='Filename for address json.')
    compile_.add_argument('-O', '--overwrite',
                          help='If address json already exists, overwrite.',
                          action='store_true', default=False)
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
        elif args.command ==  'update':
            update_externs(args.source, args.controller)
        elif args.command == 'upgrade':
            upgrade_controller(args.source, args.controller)
        else:
            raise LoadContractsError('command not implemented: {cmd}', cmd=args.command)
    except LoadContractsError as exc:
        print(exc)
        return 1
    else:
        return 0


if __name__ == '__main__':
    sys.exit(main())
