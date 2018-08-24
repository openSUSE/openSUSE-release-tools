#!/usr/bin/python2
# check if all packages in a repo are newer than all other repos

import sys
import os
import re
import solv

pool = solv.Pool()
args = sys.argv[1:]
if len(args) < 2:
    print("usage: checknewer NEWREPO OLDREPO1 [OLDREPO2...]")
    sys.exit(1)

firstrepo = None
for arg in args:
    argf = solv.xfopen(arg)
    repo = pool.add_repo(arg)
    if not firstrepo:
        firstrepo = repo
    if re.search(r'solv$', arg):
        repo.add_solv(argf)
    elif re.search(r'primary\.xml', arg):
        repo.add_rpmmd(argf, None)
    elif re.search(r'packages', arg):
        repo.add_susetags(argf, 0, None)
    else:
        print("%s: unknown repo type" % (arg))
        sys.exit(1)

# we only want self-provides
for p in pool.solvables:
    if p.archid == solv.ARCH_SRC or p.archid == solv.ARCH_NOSRC:
        continue
    selfprovides = pool.rel2id(p.nameid, p.evrid, solv.REL_EQ)
    p.unset(solv.SOLVABLE_PROVIDES)
    p.add_deparray(solv.SOLVABLE_PROVIDES, selfprovides)

pool.createwhatprovides()

for p in firstrepo.solvables:
    newerdep = pool.rel2id(p.nameid, p.evrid, solv.REL_GT | solv.REL_EQ)
    for pp in pool.whatprovides(newerdep):
        if pp.repo == firstrepo:
            continue
        if p.nameid != pp.nameid:
            continue
        if p.identical(pp):
            continue
        if p.archid != pp.archid and p.archid != solv.ARCH_NOARCH and pp.archid != solv.ARCH_NOARCH:
            continue
        src = p.name
        if not p.lookup_void(solv.SOLVABLE_SOURCENAME):
            src = p.lookup_str(solv.SOLVABLE_SOURCENAME)
        if src is None:
            src = "?"
        print("%s: %s is older than %s from %s" % (src, p, pp, pp.repo))
