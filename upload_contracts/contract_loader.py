from __future__ import print_function
from .state import State
from .compile_proc import CompileProc
from .dir_utils import TempDirCopy, SERPENT_EXT, path_to_name
from .commands import update_externs
from ethereum.tester import DEFAULT_ACCOUNT
import sha3
import rlp
import os
import shutil
from binascii import hexlify
import serpent


def address(creator, nonce):
    return sha3.keccak_256(rlp.encode([creator, nonce])).digest()[12:]


class ContractLoaderError(Exception):
    """Used for error that occur while setting up contracts."""


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
        self._state = State()
        self._paths = {}
        self._temp_dir = None

    def __getattr__(self, name):
        """Use it like a namedtuple!"""
        return getattr(self._state, name)

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
        elif os.path.isfile(contract):
            source_path = os.path.abspath(contract)
            assert source_path.startswith(temp_dir.source_dir), 'path not in the source directory!'
            path_in_temp_dir = source_path.replace(temp_dir.source_dir, temp_dir.temp_source_dir, 1)
        else:
            raise ContractLoaderError('The contract is not a known contract, or it\'s path is outside the source directory!')

        if os.path.isfile(path_in_temp_dir):
            os.remove(path_in_temp_dir)
            
        shutil.copy2(source_path, path_in_temp_dir)
        update_externs(temp_dir.temp_source_dir, '0x' + hexlify(self.controller.address).decode())
        name = path_to_name(path_in_temp_dir)
        compiled_code = serpent.compile(path_in_temp_dir)
        interface = serpent.mk_full_signature(path_in_temp_dir)
        self._state.abi_contract(name, compiled_code, interface)
        self._paths[name] = path_in_temp_dir

    def save_state(self, save_path):
        self._state.save(save_path)

    def load_from_save(self, source_dir, save_path):
        """Loads a snapshot."""
        self._state = State.load(save_path)
        self._temp_dir = TempDirCopy(source_dir)
        self._paths = {path_to_name(p): p for p in self._temp_dir.find_files(SERPENT_EXT)}

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
        serpent_paths = self._temp_dir.find_files(SERPENT_EXT)
        self._paths.update({path_to_name(p):p for p in serpent_paths})
        controller_nonce = self.state.state.get_nonce(DEFAULT_ACCOUNT)
        controller_address = '0x' + hexlify(address(DEFAULT_ACCOUNT, controller_nonce)).decode()
        print('updating externs')
        update_externs(self._temp_dir.temp_source_dir, controller_address)

        results = CompileProc.compile_paths(serpent_paths, verbose=True)

        for result in results:
            path = result.pop('path')
            if os.path.basename(path) == controller:
                print('creating controller')
                self._state.abi_contract(path_to_name(path),
                                         **result)
                break
        else:
            raise ContractLoaderError('Unable to find controller: {}', controller)

        print('Creating special contracts')
        for filename in specials:
            for result in results:
                path = result.pop('path')
                if os.path.basename(path) == filename:
                    self._state.abi_contract(path_to_name(path),
                                             **result)
                    break
            else:
                raise ContractLoaderError('Special contract not found: {}', filename)

        print('Creating other contracts')
        for result in results:
            path = result.pop('path')
            name = path_to_name(path)
            if name not in self._state.contracts:
                self._state.abi_contract(name, **result)
