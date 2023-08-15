NAME = 'openSUSE-release-tools'


def version_calculate():
    from os import path
    # osclib is most likely "installed" via a symlink in ~/.osc-plugins
    # => need to resolve the relative path
    osc_release_tools_dir = path.abspath(path.join(path.realpath(path.dirname(__file__)), ".."))
    if path.exists(path.join(osc_release_tools_dir, ".git")):
        from osclib.git import describe
        try:
            return describe(directory=osc_release_tools_dir)
        except FileNotFoundError:
            pass  # Fall through to final return.

    return '0.0.0-dev'


VERSION = version_calculate()
