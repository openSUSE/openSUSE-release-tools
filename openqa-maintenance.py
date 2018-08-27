#!/usr/bin/python

from oqamaint.cli import CommandLineInterface

import sys

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
