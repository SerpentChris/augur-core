import os
import time
import serpent
from load_contracts import mk_signature

def extern_eq(e1, e2):
    """Checks if two serpent externs are equivalent."""
    i1 = e1.find(': [')
    i2 = e2.find(': [')

    funcs1 = set(e1[i1+3:-1].split(', '))
    funcs2 = set(e2[i2+3:-1].split(', '))
    return sorted(list((funcs1 - funcs2)|(funcs2 - funcs1)))

if __name__ == '__main__':
    source_dir = os.path.abspath('../src')
    print('Generating serpent signatures for code in', source_dir)
    print()

    start1 = time.time()
    sigs1 = {}
    for directory, subdirs, files in os.walk(source_dir):
        for filename in files:
            if filename.endswith('.se'):
                path = os.path.join(directory, filename)
                sigs1[path] = mk_signature(path)
    print('load_contracts.mk_signature took %10.5f seconds.' % (time.time() - start1))

    start2 = time.time()
    sigs2 = {}
    for directory, subdirs, files in os.walk(source_dir):
        for filename in files:
            if filename.endswith('.se'):
                path = os.path.join(directory, filename)
                sigs2[path] = serpent.mk_signature(path).decode()
    print('serpent.mk_signature took %10.5f seconds.' % (time.time() - start2))
    print()
    print('Differing sigs:')
    for path in sigs1:
        s1 = sigs1[path]
        s2 = sigs2[path]
        result = extern_eq(s1, s2)
        if result:
            print('Differing signatures generated for', path)
            print(result)
            print()
