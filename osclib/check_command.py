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
        state = self.api.check_project_status(project)

        # If the project is empty just skip it
        if not state:
            return False

        print('Checking staging project: {}'.format(project))
        if type(state) is list:
            print(' -- Project still needs attention')
            for issue in state:
                print(issue)
        else:
            print(' ++ Acceptable staging project')

        return True

    def _report(self, project, verbose):
        """Print a single report for a project.
        :param project: dict object, converted from JSON.
        :param verbose: do verbose check or not

        """
        report = []

        # Check for superseded requests
        report.extend('   - Request %s is superseded by %s' % (r['number'], r['superseded_by'])
                      for r in project['obsolete_requests'] if r['state'] == 'superseded')

        # Untracked requests
        report.extend('   - Request %s is no tracked but is open for the project' % r['number']
                      for r in project['untracked_requests'])

        # Status of obsolete requests
        for r in project['obsolete_requests']:
            if r['state'] == 'superseded':
                continue
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
        if self.api.copenqa:
            if not project['openqa_jobs']:
                report.append('   - No openQA result yet')
            for job in project['openqa_jobs']:
                if job['result'] != 'passed':
                    qa_result = job['result'] if job['result'] != 'none' else 'running'
                    report.append("   - openQA's overall status is %s for %s/tests/%s" % (qa_result, self.api.copenqa, job['id']))
                    report.extend('     %s: fail' % module['name'] for module in job['modules'] if module['result'] == 'fail')
                    break

        if project['overall_state'] == 'acceptable':
            report.insert(0, ' ++ Acceptable staging project %s' % project['name'])
        elif project['overall_state'] != 'empty':
            report.insert(0, ' -- %s Project %s still needs attention' % (project['overall_state'].upper(),
                                                                          project['name']))

        return report

    def _check_project(self, project=None):
        """
        Check state of one specified staging project
        :param project: project to check

        """
        report = []

        info = self.api.project_status(project, not project)
        if not project:
            for prj in info:
                if not prj['selected_requests']:
                    continue
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
            print('\n'.join(self._check_project(project)))

        return True
