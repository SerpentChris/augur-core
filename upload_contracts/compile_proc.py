from __future__ import print_function
from multiprocessing import Process, Queue, cpu_count


class CompilationFailure(Exception):
    """Internal error used for collecting info about failed compiles."""
    def __init__(self, errors):
        msg = ['The following errors occured while compiling:']
        for error in errors:
            msg.append('    ' + error['result'])
        Exception.__init__(self, '\n'.join(msg))


class CompileProc(Process):
    """Create processes which compile serpent contracts."""
    def __init__(self, work, results, *args, **kwds):
        Process.__init__(self, *args, **kwds)
        self.work = work
        self.results = results

    def run(self):
        import serpent
        import os

        while True:
            path = self.work.get()

            if path == 'DONE':
                return

            try:
                assert os.path.isfile(path), 'CompleProc only works on paths!'
                compiled_code = serpent.compile(path)
                abi_interface = serpent.mk_full_signature(path)
            except Exception as exc:
                result = {'status': 'error',
                          'result': str(exc)}
            else:
                result = {'status': 'ok',
                          'result': {'compiled_code': compiled_code,
                                     'interface': abi_interface,
                                     'path': path}}
            finally:
                self.results.put(result)

    @classmethod
    def compile_paths(cls, paths, verbose=False):
        work = Queue()
        results = Queue()
        num_procs = cpu_count()
        procs = []
        code_info = []
        errors = []

        for path in paths:
            work.put(path)

        for i in range(num_procs):
            procs.append(cls(work, results))
            procs[-1].start()

        for _ in paths:
            result = results.get()
            if result['status'] == 'error':
                if verbose:
                    print(result['result'])
                errors.append(result)
            elif result['status'] == 'ok':
                if verbose:
                    print(result['result']['path'], 'compiled successfully')
                code_info.append(result['result'])
            else:
                raise Exception('WAT')

        for _ in procs:
            work.put('DONE')

        for proc in procs:
            proc.join()

        if errors:
            raise CompilationFailure(errors)

        return results
