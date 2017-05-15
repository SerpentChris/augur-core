import ethereum.tester
from ethereum.config import Env
from ethereum.state import State as _State
from binascii import hexlify, unhexlify
import json


class ContractCreationFailed(Exception):
    """Error class raised by State"""


class State(ethereum.tester.state):
    """A class like ethereum.tester.state, but with better snapshot support."""
    random_seed = 3**160
    tx_gas_limit = 4100000
    block_gas_limit = 10**9

    def __init__(self):
        ethereum.tester.state.__init__(self)
        self.state.gas_limit = State.block_gas_limit
        self._seed = State.random_seed
        self.contracts = {}

    def _rand(self):
        self._seed = pow(self._seed, 2, 2**512)
        return self._seed%2**256

    def mine(self, blocks=1):
        """Increments the block number and timestamp."""
        for i in range(blocks):
            self.state.block_number += 1
            self.state.timestamp += 6 + self._rand()%12
            self.state.delta_balance(self.state.block_coinbase, 5*10**18)
            self.state.commit()
        self.state.gas_used = 0

    def save(self, path):
        """Save a snapshot of the state along with contract metadata."""
        save_ = {'state': self.snapshot(),
                 'random_seed': self._seed,
                 'contracts': {name: str(contract) for name, contract in
                               self.contracts.items()}}

        with open(path, 'w') as f:
            json.dump(save_, f)

    @classmethod
    def load(cls, path):
        with open(path) as f:
            save_ = json.load(f)

        state = cls()
        state.env = Env()
        state.state = _State.from_snapshot(save_['state'], state.env)
        state._seed = save_['random_seed']
        for name, info in save_['contracts']:
            state.contracts[name] = Contract(state, **info)

        return state

    def __getattr__(self, name):
        try:
            return self.contracts[name]
        except KeyError:
            raise AttributeError('State objects don\'t have a {!r} attribute.'.format(name))

    def abi_contract(self, name, compiled_code, interface, **kwds):
        sender = kwds.get('sender', ethereum.tester.DEFAULT_KEY)
        endowment = kwds.get('endowment', 0),
        gas = kwds.get('gas', State.tx_gas_limit)
        address = self.evm(compiled_code,
                           sender=sender,
                           endowment=endowment,
                           gas=gas)

        if len(self.state.get_code(address)) == 0:
            raise ContractCreationFailed('Failed to create contract {!r}'.format(name))

        self.contracts[name] = Contract(self, interface, address)

        return self.contracts[name]


class Contract(object):
    """A class similar to ABIContract that is nicer with multiprocessing."""

    def __init__(self, state, interface, address):
        """Initializes a Contract object.

        Arguments:
        state -- a tester_state.State instance.
        interface -- the ABI interface a.k.a. full signature of the contract.
        address -- an address in the form of a hex encoded str.
        """
        self._translator = ethereum.abi.ContractTranslator(interface)
        self._interface = interface
        self._address = unhexlify(address)
        self._functions = {}

        for item in interface:
            if item['type'] == 'function':
                name = item['name']
                self._functions[name] = ContractFunc(state,
                                                     name,
                                                     address,
                                                     self._translator)

    def __getattr__(self, name):
        if name in self._functions:
            return self._functions[name]
        else:
            raise AttributeError('{!r} not a valid function name'.format(name))

    def __str__(self):
        return {'interface': self._full_sig,
                'address': hexlify(self._address).decode()}


class ContractFunc(object):
    """An internal class used to make ABI contract functions."""

    def __init__(self, state, func_name, address, translator):
        self.state = state
        self.func_name = func_name
        self.address = address
        self.translator = translator

    def __call__(self, *args, **kwds):
        encoded_call = self.translator.encode_function_call(self.func_name, args)
        
        sender = kwds.get('sender', ethereum.tester.DEFAULT_KEY)
        value = kwds.get('value', 0)
        gas = kwds.get('gas', self.state.tx_gas_limit)
        profiling = kwds.get('profiling', 0)
        output = kwds.get('output', '')

        result = self.state._send(
            sender,
            self.address,
            value,
            gas=gas,
            evmdata=encoded_call,
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
        name = self.func_name
        address = hexlify(self.address).decode()
        return '<function {} at address {}>'.format(name, address)
