#!/usr/bin/python

from __future__ import print_function

from pprint import pprint
import os
import sys
import re
import logging
import cmdln

from fnmatch import fnmatch
from ConfigParser import SafeConfigParser
import solv
import rpm

logger = None

REASONS = dict([(getattr(solv.Solver, i), i[14:]) for i in dir(solv.Solver) if i.startswith('SOLVER_REASON_')])


class DepTool(cmdln.Cmdln):
    def __init__(self, *args, **kwargs):
        cmdln.Cmdln.__init__(self, args, kwargs)

    def get_optparser(self):
        parser = cmdln.CmdlnOptionParser(self)
        parser.add_option("--dry", action="store_true", help="dry run")
        parser.add_option("--debug", action="store_true", help="debug output")
        parser.add_option("--verbose", action="store_true", help="verbose")
        parser.add_option("--system", action="store_true", help="with system repo")
        parser.add_option("--arch", dest="arch", help="architecture", default='x86_64')
        return parser

    def postoptparse(self):
        level = None
        if self.options.debug:
            level = logging.DEBUG
        elif self.options.verbose:
            level = logging.INFO

        logging.basicConfig(level=level)

        global logger
        logger = logging.getLogger()

    def prepare_pool(self, repos):

        self.pool = solv.Pool()
        self.pool.setarch(self.options.arch)

        self._read_repos(repos)

        if self.options.system:
            self._add_system_repo()

        self.pool.addfileprovides()
        self.pool.createwhatprovides()

    def _read_repos(self, repos):
        repodir = '/etc/zypp/repos.d'
        solvfile = '/var/cache/zypp/solv/%s/solv'

        parser = SafeConfigParser()

        if not repos:
            repos = [f for f in os.listdir(repodir) if fnmatch(f, '*.repo')]

        for r in repos:
            if '/' in r or r.endswith('.solv'):
                name = os.path.basename(os.path.splitext(r)[0])
                repo = self.pool.add_repo(name)
                repo.add_solv(r)
                logger.debug("add repo %s" % name)
            else:
                try:
                    if r.endswith('.repo'):
                        name = os.path.splitext(r)[0]
                    else:
                        name = r
                        r += '.repo'
                    parser.read('/'.join((repodir, r)))
                    if parser.get(name, 'enabled') == '1':
                        repo = self.pool.add_repo(name)
                        repo.add_solv(solvfile % name)
                        if parser.has_option(name, 'priority'):
                            repo.priority = parser.getint(name, 'priority')
                        logger.debug("add repo %s" % name)
                except Exception, e:
                    logger.error(e)

    def _add_system_repo(self):
        solvfile = '/var/cache/zypp/solv/@System/solv'
        repo = self.pool.add_repo('system')
        repo.add_solv(solvfile)

    @cmdln.option("-s", "--single", action="store_true",
                  help="single step all requires/recommends")
    @cmdln.option("--size", action="store_true",
                  help="print installed size")
    @cmdln.option("-l", "--lock", dest="lock", action="append",
                  help="packages to lock")
    @cmdln.option("-r", "--repo", dest="repo", action="append",
                  help="repo to use")
    @cmdln.option("--explain", dest="explain", action="append",
                  help="rule to explain")
    @cmdln.option("--solver-debug", action="store_true",
                  help="debug solver")
    @cmdln.option("--ignore-recommended", action="store_true",
                  help="ignore recommended")
    def do_install(self, subcmd, opts, *args):
        """${cmd_name}: generate pot file for patterns

        ${cmd_usage}
        ${cmd_option_list}
        """

        locked = []
        if opts.lock:
            for l in opts.lock:
                for i in l.split(','):
                    locked.append(i)

        good = True

        self.prepare_pool(opts.repo)
        if opts.solver_debug:
            self.pool.set_debuglevel(3)

        def solveit(packages):
            jobs = []
            for l in locked:
                sel = self.pool.select(str(l), solv.Selection.SELECTION_NAME)
                if sel.isempty():
                    # if we can't find it, it probably is not as important
                    logger.debug('locked package {} not found'.format(l))
                else:
                    jobs += sel.jobs(solv.Job.SOLVER_LOCK)

            for n in packages:
                sel = self.pool.select(str(n), solv.Selection.SELECTION_NAME)
                if sel.isempty():
                    logger.error('package {} not found'.format(n))
                jobs += sel.jobs(solv.Job.SOLVER_INSTALL)

            solver = self.pool.Solver()

            if opts.ignore_recommended:
                solver.set_flag(solver.SOLVER_FLAG_IGNORE_RECOMMENDED, 1)

            problems = solver.solve(jobs)
            if problems:
                for problem in problems:
                    logger.error('%s', problem)
                return False

            trans = solver.transaction()
            if trans.isempty():
                logger.error('nothing to do')
                return False

            for s in trans.newsolvables():
                print(','.join(packages), s.name)
                if opts.explain and s.name in opts.explain:
                    reason, rule = solver.describe_decision(s)
                    ruleinfo = None
                    if rule:
                        ruleinfo = rule.info().problemstr()
                    if reason == solv.Solver.SOLVER_REASON_WEAKDEP:
                        for v in solver.describe_weakdep_decision(s):
                            reason2, s2, dep = v
                            print("-> %s %s %s" % (s2.name, REASONS[reason2], dep))
                    else:
                        print("-> %s %s %s" % (s.name, REASONS[reason], ruleinfo))

            if opts.size:
                size = trans.calc_installsizechange()
                print("SIZE %s" % (size))

            return True

        if opts.single:
            for n in args:
                sel = self.pool.select(str(n), solv.Selection.SELECTION_NAME)
                for s in sel.solvables():
                    deps = s.lookup_deparray(solv.SOLVABLE_RECOMMENDS)
                    deps += s.lookup_deparray(solv.SOLVABLE_REQUIRES)
                    for dep in deps:
                        # only add recommends that exist as packages
                        rec = self.pool.select(dep.str(), solv.Selection.SELECTION_NAME)
                        if not rec.isempty():
                            if not solveit([dep.str()]):
                                good = False
        else:
            if not solveit(args):
                good = False

        if not good:
            logger.error("solver errors encountered")
            return 1

    @cmdln.option("-r", "--repo", dest="repo", action="append",
                  help="repo to use")
    def do_deps(self, subcmd, opts, *packages):
        """${cmd_name}: show package deps

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.prepare_pool(opts.repo)

        for n in packages:
            sel = self.pool.select(n, solv.Selection.SELECTION_NAME)
            if sel.isempty():
                logger.error("%s not found", n)
            for s in sel.solvables():
                print('- {}-{}@{}:'.format(s.name, s.evr, s.arch))
                for kind in ('RECOMMENDS', 'REQUIRES', 'SUPPLEMENTS', 'ENHANCES', 'PROVIDES', 'SUGGESTS'):
                    deps = s.lookup_deparray(getattr(solv, 'SOLVABLE_'+kind), 0)
                    if deps:
                        print('  {}:'.format(kind))
                        for dep in deps:
                            print('    - {}'.format(dep))

    @cmdln.option("-r", "--repo", dest="repo", action="append",
                  help="repo to use")
    def do_whatprovides(self, subcmd, opts, *relation):
        """${cmd_name}: list packages providing given relations

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.prepare_pool(opts.repo)

        for r in relation:
            i = self.pool.str2id(r)
            for s in self.pool.whatprovides(i):
                print('- {}-{}@{}:'.format(s.name, s.evr, s.arch))

    @cmdln.option("-r", "--repo", dest="repo", action="append",
                  help="repo to use")
    def do_patterns(self, subcmd, opts, *relation):
        """${cmd_name}: list patterns

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.prepare_pool(opts.repo)

        patternid = self.pool.str2id('pattern()')
        for s in self.pool.whatprovides(patternid):
            deps = s.lookup_deparray(solv.SOLVABLE_PROVIDES)
            order = 0
            for dep in deps:
                name = str(dep)
                if name.startswith('pattern-order()'):
                    # XXX: no function in bindings to do that properly
                    order = name[name.find('= ')+2:]
            print("{} {}".format(order, s.name))

    @cmdln.option("--providers", action="store_true",
                  help="also show other providers")
    @cmdln.option("--relation", action="store_true",
                  help="arguments are relations rather than package names")
    @cmdln.option("-r", "--repo", dest="repo", action="append",
                  help="repo to use")
    def do_rdeps(self, subcmd, opts, *args):
        """${cmd_name}: list packages that require, recommend etc the given packages

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.prepare_pool(opts.repo)

        kinds = ['RECOMMENDS', 'REQUIRES', 'SUPPLEMENTS', 'ENHANCES', 'SUGGESTS']
        if opts.providers:
            kinds.append('PROVIDES')

        for kind in kinds:
            kindid = getattr(solv, 'SOLVABLE_'+kind, 0)
            kindprinted = False
            if opts.relation:
                # FIXME: doesnt work
                for r in args:
                    sel = self.pool.matchdeps(r, solv.Selection.SELECTION_REL | solv.Selection.SELECTION_FLAT, kindid)
                    if sel.isempty():
                        logger.info('nothing %s %s', kind.lower(), r)
                        continue
                    for s in sel.solvables():
                        print('  {}: {}-{}@{}'.format(r, s.name, s.evr, s.arch))
            else:
                for n in args:
                    sel = self.pool.select(n, solv.Selection.SELECTION_NAME)
                    if sel.isempty():
                        logger.error("%s not found", n)
                        continue
                    for s in sel.solvables():
                            prov = s.lookup_deparray(solv.SOLVABLE_PROVIDES, 0)
                            if not prov:
                                logger.error("%s doesn't provide anything")
                                continue
                            for p in prov:
                                sel = self.pool.matchdepid(p, solv.Selection.SELECTION_REL | solv.Selection.SELECTION_FLAT, kindid)
                                if sel.isempty():
                                    logger.debug('nothing %s %s', kind.lower(), p)
                                    continue
                                for r in sel.solvables():
                                    if kindid == solv.SOLVABLE_PROVIDES and r == s:
                                        continue
                                    if not kindprinted:
                                        print(kind)
                                        kindprinted = True
                                    print('  {}: {}-{}@{}'.format(p, r.name, r.evr, r.arch))

    @cmdln.option("-r", "--repo", dest="repo", action="append",
                  help="repo to use")
    def do_what(self, subcmd, opts, *relation):
        """${cmd_name}: list packages that have dependencies on given relation

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.prepare_pool(opts.repo)

        kinds = ['PROVIDES', 'RECOMMENDS', 'REQUIRES', 'SUPPLEMENTS', 'ENHANCES', 'SUGGESTS']

        for r in relation:
            p = self.pool.str2id(r)
            for kind in kinds:
                kindprinted = False
                kindid = getattr(solv, 'SOLVABLE_'+kind, 0)
                sel = self.pool.matchdepid(p, solv.Selection.SELECTION_REL | solv.Selection.SELECTION_FLAT, kindid)
                if sel.isempty():
                    logger.debug('nothing %s %s', kind.lower(), p)
                    continue
                for r in sel.solvables():
                    if not kindprinted:
                        print(kind)
                        kindprinted = True
                    print('  {}-{}@{}'.format(r.name, r.evr, r.arch))

    @cmdln.option("-r", "--repo", dest="repo", action="append",
                  help="repo to use")
    def do_info(self, subcmd, opts, *args):
        """${cmd_name}: show some info about a package

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.prepare_pool(opts.repo)

        sattrs = [s for s in dir(solv) if s.startswith("SOLVABLE_")]
        for n in args:
            sel = self.pool.select(str(n), solv.Selection.SELECTION_NAME)
            for s in sel.solvables():
                for attr in sattrs:
                    sid = getattr(solv, attr, 0)
                    # pretty stupid, just lookup strings
                    value = s.lookup_str(sid)
                    if value:
                        print('{}: {}'.format(attr[len('SOLVABLE_'):], value))


if __name__ == "__main__":
    app = DepTool()
    sys.exit(app.main())
else:
    logger = logging.getLogger()
