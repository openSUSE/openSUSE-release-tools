#!/usr/bin/python

from pkglistgen.cli import CommandLineInterface

import sys

app = CommandLineInterface()
sys.exit(app.main())
