import os
import re

FUNCTION = re.compile('^def (.+)\((.*)\):$', re.M)
RETURN = re.compile('^\s*[^#].+return\((.*)\).*$', re.M)
IGNORE = ('any', 'init', 'shared')
ARGSEP = re.compile(',\s?')
TYPE_INFO = re.compile(
    '^(u?int|u?fixed|address|bool|bytes|string|function)'
    '(\d+x\d+|\d+)?'
    '(\[\d*\])?$')


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


def mk_signature(code, contract_name='main'):
    """Experimental replacement for serpent.mk_signature.

    Note: This function assumes that insets never introduce
    new functions to the code, and also that macros don't 
    introduce new return types to code. This means that this
    function may be unable to determine if a single return-type
    exists, though this isn't used by the ABI and the default
    return type for this situation, '_', is used.
    """

    # replicate serpent.mk_signature behavior: paths and strings of code are accepted.
    if os.path.isfile(code):
        with open(code) as f:
            code = f.read()

    func_matches = list(FUNCTION.finditer(code))
    funcs = []
    l = len(func_matches)
    for i, match in enumerate(func_matches):
        func_name, args = match.groups()
        if func_name not in IGNORE:
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
            funcs.append(func_name + ':[' + cannonical_types + ']:' + return_type)
    return 'extern {}: [{}]'.format(contract_name, ', '.join(funcs))
