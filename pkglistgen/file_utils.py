import glob
import os
import os.path
import shutil

from lxml import etree as ET

def move_list(file_list, destination):
    for name in file_list:
        os.rename(name, os.path.join(destination, os.path.basename(name)))

def unlink_all_except(path, ignore_list=['_service'], ignore_hidden=True):
    for name in os.listdir(path):
        if name in ignore_list or (ignore_hidden and name.startswith('.')):
            continue

        name_path = os.path.join(path, name)
        if os.path.isfile(name_path):
            os.unlink(name_path)

def copy_directory_contents(source, destination, ignore_list=[]):
    for name in os.listdir(source):
        name_path = os.path.join(source, name)
        if name in ignore_list or not os.path.isfile(name_path):
            continue

        shutil.copy(name_path, os.path.join(destination, name))

def change_extension(path, original, final):
    for name in glob.glob(os.path.join(path, '*{}'.format(original))):
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

