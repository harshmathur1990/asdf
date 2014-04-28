# Licensed under a 3-clause BSD style license - see LICENSE.rst
# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, unicode_literals, print_function

import re

from . import block
from . import constants
from .tags.finf import FinfObject
from . import generic_io
from . import versioning
from . import yamlutil


class FinfFile(versioning.VersionedMixin):
    """
    The main class that represents a FINF file.
    """
    def __init__(self, tree=None):
        """
        Parameters
        ----------
        tree : dict, optional
            The main tree data in the FINF file.  Must conform to the
            FINF schema.
        """
        self._blocks = block.BlockManager(self)
        if tree is None:
            tree = {}
        self.tree = tree
        self._fd = None

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        if self._fd:
            # This is ok to always do because GenericFile knows
            # whether it "owns" the file and should close it.
            self._fd.close()

    @property
    def uri(self):
        if self._fd is not None:
            return self._fd._uri
        return None

    @property
    def tree(self):
        """
        Get the tree of data in the FINF file.

        When setting, the tree will be validated against the FINF
        schema.
        """
        return self._tree

    @tree.setter
    def tree(self, tree):
        yamlutil.validate(tree, self)

        self._tree = FinfObject(tree)

    @property
    def blocks(self):
        """
        Get the list of blocks in the FINF file.  This is a low-level
        detail that is not required for most uses.
        """
        return self._blocks

    @classmethod
    def _parse_header_line(cls, line):
        """
        Parses the header line in a FINF file to obtain the FINF version.
        """
        regex = (constants.FINF_MAGIC +
                 b'(?P<major>[0-9]+)\.(?P<minor>[0-9]+)\.(?P<micro>[0-9]+)')
        match = re.match(regex, line)
        if match is None:
            raise IOError("Does not appear to be a FINF file.")
        return (int(match.group("major")),
                int(match.group("minor")),
                int(match.group("micro")))

    @classmethod
    def read(cls, fd, uri=None, mode='r', _get_yaml_content=False):
        """
        Read a FINF file.

        Parameters
        ----------
        fd : string or file-like object
            May be a string ``file`` or ``http`` URI, or a Python
            file-like object.

        uri : string, optional
            The URI of the file.  Only required if the URI can not be
            automatically determined from `fd`.

        mode : string, optional
            The mode to open the file in.  Must be ``r`` (default) or
            ``rw``.

        Returns
        -------
        finffile : FinfFile
            The new FinfFile object.
        """
        fd = generic_io.get_file(fd, mode=mode, uri=uri)

        self = cls()
        self._fd = fd

        header_line = fd.read_until(b'\r?\n', "newline", include=True)
        self.version = cls._parse_header_line(header_line)

        yaml_token = fd.read(4)
        yaml_content = b''
        has_blocks = False
        if yaml_token == b'%YAM':
            # The yaml content is read now, but we parse it after finding
            # all of the blocks, so that arrays can be resolved to their
            # blocks immediately.
            yaml_content = yaml_token + fd.read_until(
                constants.YAML_END_MARKER_REGEX, 'End of YAML marker',
                include=True)
            has_blocks = fd.seek_until(constants.BLOCK_MAGIC, include=True)
        elif yaml_token == constants.BLOCK_MAGIC:
            has_blocks = True
        elif yaml_token != b'':
            raise IOError("FINF file appears to contain garbage after header.")

        # For testing: just return the raw YAML content
        if _get_yaml_content:
            fd.close()
            return yaml_content

        if has_blocks:
            self._blocks.read_internal_blocks(fd, past_magic=True)

        if len(yaml_content):
            ctx = yamlutil.Context(self)
            tree = yamlutil.load_tree(yaml_content, ctx)
            ctx.run_hook(tree, 'post_read')
            self._tree = tree
        else:
            self._tree = {}

        return self

    def update(self):
        """
        Update the file on disk in place.
        """
        raise NotImplementedError()

    def write_to(self, fd, exploded=False):
        """
        Write the FINF file to the given file-like object.

        Parameters
        ----------
        fd : string or file-like object
            May be a string path to a file, or a Python file-like
            object.
        """
        ctx = yamlutil.Context(self, options={
            'exploded': exploded})

        with generic_io.get_file(fd, mode='w') as fd:
            if exploded and fd.uri is None:
                raise ValueError(
                    "Can not write an exploded file without knowing its URI.")

            tree = self._tree

            try:
                # This is where we'd do some more sophisticated block
                # reorganization, if necessary
                self._blocks.finalize(ctx)

                fd.write(constants.FINF_MAGIC)
                fd.write(self.version_string.encode('ascii'))
                fd.write(b'\n')

                if len(tree):
                    ctx.run_hook(tree, 'pre_write')
                    yamlutil.dump_tree(tree, fd, ctx)

                self.blocks.write_blocks(fd)
            finally:
                if len(tree):
                    ctx.run_hook(tree, 'post_write')

            fd.flush()