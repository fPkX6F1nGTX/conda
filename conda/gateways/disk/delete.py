# -*- coding: utf-8 -*-
# Copyright (C) 2012 Anaconda, Inc
# SPDX-License-Identifier: BSD-3-Clause
from __future__ import absolute_import, division, print_function, unicode_literals

from errno import ENOENT
import fnmatch
from logging import getLogger
from os import rename, unlink, walk, makedirs, getcwd, rmdir, listdir
from os.path import abspath, dirname, isdir, join, split
import shutil
from subprocess import Popen, PIPE, check_call
import sys

from . import MAX_TRIES, exp_backoff_fn
from .link import islink, lexists
from .permissions import make_writable, recursive_make_writable
from ...base.context import context
from ...common.compat import on_win


log = getLogger(__name__)


def rmtree(path, *args, **kwargs):
    # subprocessing to delete large folders can be quite a bit faster
    if on_win:
        check_call('rd /s /q "{}"'.format(path), shell=True)
    else:
        try:
            makedirs('.empty')
        except:
            pass
        # yes, this looks strange.  See
        #    https://unix.stackexchange.com/a/79656/34459
        #    https://web.archive.org/web/20130929001850/http://linuxnote.net/jianingy/en/linux/a-fast-way-to-remove-huge-number-of-files.html
        args = ['rsync', '-a', '--delete', join(getcwd(), '.empty') + "/", path + "/"]
        print(' '.join(args))
        check_call(['rsync', '-a', '--delete', join(getcwd(), '.empty') + "/", path + "/"])
        shutil.rmtree('.empty')
    rmdir(path)


def unlink_or_rename_to_trash(path):
    try:
        make_writable(path)
        unlink(path)
    except (OSError, IOError) as e:
        if on_win:
            condabin_dir = join(context.conda_prefix, "condabin")
            trash_script = join(condabin_dir, 'rename_trash.bat')
            _dirname, _fn = split(path)
            p = Popen(['cmd.exe', '/C', trash_script, _dirname, _fn], stdout=PIPE, stderr=PIPE)
            stdout, stderr = p.communicate()
        else:
            rename(path, path + ".trash")


def remove_empty_parent_paths(path):
    # recurse to clean up empty folders that were created to have a nested hierarchy
    parent_path = dirname(path)
    while(isdir(parent_path) and not listdir(parent_path)):
        rmdir(parent_path)
        parent_path = dirname(parent_path)


def rm_rf(path, max_retries=5, trash=True, clean_empty_parents=False, *args, **kw):
    """
    Completely delete path
    max_retries is the number of times to retry on failure. The default is 5. This only applies
    to deleting a directory.
    If removing path fails and trash is True, files will be moved to the trash directory.
    """
    try:
        path = abspath(path)
        log.trace("rm_rf %s", path)
        if isdir(path) and not islink(path):
            backoff_rmdir(path)
        elif lexists(path):
            unlink_or_rename_to_trash(path)
        else:
            log.trace("rm_rf failed. Not a link, file, or directory: %s", path)
    finally:
        if lexists(path):
            log.info("rm_rf failed for %s", path)
            return False
    if clean_empty_parents:
        remove_empty_parent_paths(path)
    return True


# aliases that all do the same thing (legacy compat)
try_rmdir_all_empty = move_to_trash = move_path_to_trash = rm_rf


def delete_trash(prefix=None):
    if not prefix:
        prefix = sys.prefix
    for root, dirs, files in walk(prefix):
        for basename in files:
            if fnmatch.fnmatch(basename, "*.trash"):
                filename = join(root, basename)
                try:
                    unlink(filename)
                except (OSError, IOError) as e:
                    log.debug("%r errno %d\nCannot unlink %s.", e, e.errno, filename)


def backoff_rmdir(dirpath, max_tries=MAX_TRIES):
    if not isdir(dirpath):
        return

    def retry(func, path, exc_info):
        if getattr(exc_info[1], 'errno', None) == ENOENT:
            return
        recursive_make_writable(dirname(path), max_tries=max_tries)
        func(path)

    def _rmdir(path):
        try:
            recursive_make_writable(path)
            exp_backoff_fn(rmtree, path, onerror=retry, max_tries=max_tries)
        except (IOError, OSError) as e:
            if e.errno == ENOENT:
                log.trace("no such file or directory: %s", path)
            else:
                raise
    try:
        rmtree(dirpath)
    # we don't really care about errors that much.  We'll catch remaining files
    #    with slower python logic.
    except:
        pass

    for root, dirs, files in walk(dirpath, topdown=False):
        for file in files:
            unlink_or_rename_to_trash(join(root, file))
        for dir in dirs:
            _rmdir(join(root, dir))

    _rmdir(dirpath)
