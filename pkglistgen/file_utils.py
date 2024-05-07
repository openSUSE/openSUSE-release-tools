import fnmatch
import glob
import os
import os.path
import shutil
import solv
import tempfile
import subprocess

from lxml import etree as ET


def copy_list(file_list, destination):
    for name in file_list:
        shutil.copy(name, os.path.join(destination, os.path.basename(name)))


def move_list(file_list, destination):
    for name in file_list:
        os.rename(name, os.path.join(destination, os.path.basename(name)))


def unlink_all_except(path, ignore_list=['_service'], ignore_hidden=True):
    """ignore_list is a list of globs"""
    for name in os.listdir(path):
        if ignore_hidden and name.startswith('.'):
            continue

        if any([fnmatch.fnmatch(name, pattern) for pattern in ignore_list]):
            continue

        name_path = os.path.join(path, name)
        if os.path.isfile(name_path):
            os.unlink(name_path)


def copy_directory_contents(source, destination, ignore_list=[]):
    for name in os.listdir(source):
        name_path = os.path.join(source, name)
        if name in ignore_list or name_path in ignore_list or not os.path.isfile(name_path):
            continue

        shutil.copy(name_path, os.path.join(destination, name))


def change_extension(path, original, final):
    for name in glob.glob(os.path.join(path, f'*{original}')):
        # Assumes the extension is only found at the end.
        os.rename(name, name.replace(original, final))


def multibuild_from_glob(destination, pathname):
    root = ET.Element('multibuild')
    for name in sorted(glob.glob(os.path.join(destination, pathname))):
        package = ET.SubElement(root, 'package')
        package.text = os.path.splitext(os.path.basename(name))[0]

    with open(os.path.join(destination, '_multibuild'), 'w+b') as f:
        f.write(ET.tostring(root, pretty_print=True))


def unlink_list(path, names):
    for name in names:
        if path is None:
            name_path = name
        else:
            name_path = os.path.join(path, name)

        if os.path.isfile(name_path):
            os.unlink(name_path)


def add_susetags(pool, file):
    oldsysrepo = pool.add_repo(file)
    defvendorid = oldsysrepo.meta.lookup_id(solv.SUSETAGS_DEFAULTVENDOR)
    f = tempfile.TemporaryFile()
    if file.endswith('.xz'):
        subprocess.call(['xz', '-cd', file], stdout=f.fileno())
    elif file.endswith('.zst'):
        subprocess.call(['zstd', '-cd', file], stdout=f.fileno())
    else:
        raise Exception("unsupported " + file)
    os.lseek(f.fileno(), 0, os.SEEK_SET)
    oldsysrepo.add_susetags(solv.xfopen_fd(None, f.fileno()), defvendorid, None,
                            solv.Repo.REPO_NO_INTERNALIZE | solv.Repo.SUSETAGS_RECORD_SHARES)
    return oldsysrepo
