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
import os
import errno
import re
import shutil
import tempfile

IMPORT = re.compile('^import (?P<name>\w+) as (?P<alias>\w+)$')
CONTROLLER_INIT = re.compile('^(?P<indent>\s*)self.controller = 0x[0-9A-F]{1,40}')
INDENT = re.compile('^$|^[#\s].*$')

SERPENT_EXT = '.se'
MACRO_EXT = '.sem'


class UploadContractsError(Exception):
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
            UploadContractsError(
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
                raise UploadContractsError(
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
        raise UploadContractsError('No functions found! Is this a macro file?')

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
