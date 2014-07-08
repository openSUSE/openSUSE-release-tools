import json


class CheckCommand(object):
    def __init__(self, api):
        self.api = api

    def _previous_check_one_project(self, project, verbose):
        """
        Check state of one specified staging project
        :param project: project to check
        :param verbose: do verbose check or not
        """
        state = self.api.check_project_status(project, verbose)

        # If the project is empty just skip it
        if not state:
            return False

        print('Checking staging project: {}'.format(project))
        if type(state) is list:
            print(' -- Project still neeeds attention')
            for issue in state:
                print(issue)
        else:
            print(' ++ Acceptable staging project')

        return True

    def _report(self, project, verbose, is_subproject=False):
        """Print a single report for a project.
        :param project: dict object, converted from JSON.
        :param verbose: do verbose check or not

        """
        report = []

        # Check for superseded requests
        report.extend('   - Request %s is superseded by %s' % (r['id'], r['superseded_by_id'])
                      for r in project['obsolete_requests'] if r['state'] == 'superseded')

        # Untracked requests
        report.extend('   - Request %s is no tracked but is open for the project' % r['id']
                      for r in project['untracked_requests'])

        # Status of obsolete requests
        for r in project['obsolete_requests']:
            report.append('   - %s: %s' % (r['package'], r['state']))
            if not verbose:
                break

        # Missing reviews
        for r in project['missing_reviews']:
            report.append('   - %s: Missing reviews: %s' % (r['package'], r['by']))
            if not verbose:
                break

        # Building repositories
        if project['building_repositories']:
            report.append('   - At least following repositories are still building:')
        for r in project['building_repositories']:
            report.append('     %s/%s: %s' % (r['repository'], r['arch'], r['state']))
            if not verbose:
                break

        # Broken packages
        if project['broken_packages']:
            report.append('   - Following packages are broken:')
        for r in project['broken_packages']:
            report.append('     %s (%s): %s' % (r['package'], r['repository'], r['state']))
            if not verbose:
                break

        # openQA results
        if not project['openqa_jobs']:
            report.append('   - No openQA result yet')
        report.extend("   - openQA's overall status is %s for https://openqa.opensuse.org/tests/%s" % (job['result'], job['id'])
                      for job in project['openqa_jobs'] if job['result'] != 'passed')
        # XXX TODO - report the failling modules

        for subproject in project['subprojects']:
            subreport = self._report(subproject, verbose, is_subproject=True)
            if subreport:
                report.append('')
                report.append(' -- For subproject %s' % subproject['name'])
                report.extend(subreport)

        if report and not is_subproject:
            report.insert(0, ' -- Project %s still neeeds attention' % project['name'])
        elif not is_subproject:
            report.append(' ++ Acceptable staging project %s' % project['name'])

        return report

    def _check_project(self, project=None):
        """
        Check state of one specified staging project
        :param project: project to check

        """
        report = []

        if project:
            url = self.api.makeurl(('factory', 'staging_projects', project + '.json'))
        else:
            url = self.api.makeurl(('factory', 'staging_projects.json'))
        info = json.load(self.api.retried_GET(url))
        if not project:
            for prj in info:
                report.extend(self._report(prj, False))
                report.append('')
        else:
            report.extend(self._report(info, True))
        return report

    def perform(self, project=None, previous=False):
        """
        Check one staging project verbosibly or all of them at once
        :param project: project to check, None for all
        """
        if previous:
            if project:
                project = self.api.prj_from_letter(project)
                self._previous_check_one_project(project, True)
            else:
                for project in self.api.get_staging_projects():
                    if self._previous_check_one_project(project, False):
                        # newline to split multiple prjs at once
                        print('')
        else:
            print '\n'.join(self._check_project(project))

        return True
