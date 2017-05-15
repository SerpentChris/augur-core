import requests
import socket
import os
import binascii
import json
from json.decoder import JSONDecodeError


class RpcError(Exception):
    """An exception for httprpc failures."""
    def __init__(self, error):
        msg = '''\
The following error was received from the RPC server:
    code: {code}
    message: {message}
'''.format(**error)
        Exception.__init__(self, msg)


class BaseRpcClient(object):
    """A base class for the other rpc client classes"""
    json_rpc_version = '2.0'

    def __init__(self, address):
        self.address = address
        self.tag = binascii.hexlify(os.urandom(4)).decode()
        self.count = 0
        self.batch = []

    def _send(self, json):
        raise NotImplementedError(self.__class__.__name__ + ' doesn\'t implement _send')

    def send(self, method, *params):
        json_body = {'jsonrpc': self.json_rpc_version,
                     'id': '{}-{}'.format(self.tag, self.count),
                     'method': method,
                     'params': params}
        self.count += 1
        response = self._send(json_body)

        if 'error' in response:
            raise RpcError(response['error'])
        else:
            return response['result']

    def add_to_batch(self, method, *params):
        json_body = {'jsonrpc': self.json_rpc_version,
                     'id': '{}-{}'.format(self.tag, self.count),
                     'method': method,
                     'params': params}
        self.count += 1
        self.batch.append(json_body)

    def send_batch(self):
        responses = self._send(self.batch)
        self.batch = []
        results = []
        for response in responses:
            if 'error' in response:
                raise RpcError(response['error'])
            else:
                results.append(response['result'])
        return results


class HttpRpcClient(BaseRpcClient):
    def _send(self, json_obj):
        return requests.post(self.address, json=json_obj).json()


class IpcError(Exception):
    """An exception used for IPC related errors.""" 


class IpcRpcClient(BaseRpcClient):
    chunk_size = 2048

    def _send(self, json_obj):
        response_buffer = bytearray()
        response = None
        try:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.connect(self.address)
        except socket.error:
            raise IpcError('Couldn\'t connect to ipc')

        connection.sendall(json.dumps(json_obj).encode())

        while True:
            response_buffer.extend(connection.recv(self.chunk_size))

            try:
                response = json.loads(response_buffer)
            except JSONDecodeError:
                continue
            else:
                break

        connection.close()
        return response
