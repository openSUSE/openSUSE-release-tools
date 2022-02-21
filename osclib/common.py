NAME = 'openSUSE-release-tools'


def version_calculate():
    from os import path
    if path.exists('.git'):
        from osclib.git import describe
        try:
            return describe()
        except FileNotFoundError:
            pass  # Fall through to final return.

    return '0.0.0-dev'


VERSION = version_calculate()
