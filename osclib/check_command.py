

class CheckCommand(object):
    def __init__(self, api):
        self.api = api

    def _report(self, project, verbose):
        """Print a single report for a project.
        :param project: dict object, converted from JSON.
        :param verbose: do verbose check or not

        """
        report = []

        # Check for superseded requests
        for r in project.findall('obsolete_requests/*'):
            if r.get('state') == 'superseded':
                report.extend('   - Request %s is superseded by %s' % (r.get('id'), r.get('superseded_by')))

        # Untracked requests
        for r in project.findall('untracked_requests/*'):
            report.extend('   - Request %s is no tracked but is open for the project' % r.get('id'))

        # Status of obsolete requests
        for r in project.findall('obsolete_requests/*'):
            if r.get('state') == 'superseded':
                continue
            report.append('   - %s: %s' % (r.get('package'), r.get('state')))
            if not verbose:
                break

        # Missing reviews
        for r in project.findall('missing_reviews/review'):
            report.append('   - %s: Missing reviews: %s' % (r.get('package'), self.api.format_review(r)))
            if not verbose:
                break

        # Building repositories
        if project.find('building_repositories/repo') is not None:
            report.append('   - At least following repositories are still building:')
        for r in project.findall('building_repositories/*'):
            report.append('     %s/%s: %s' % (r.get('repository'), r.get('arch'), r.get('state')))
            if not verbose:
                break

        # Broken packages
        if project.find('broken_packages/package') is not None:
            report.append('   - Following packages are broken:')
        for r in project.findall('broken_packages/package'):
            report.append('     %s (%s): %s' % (r.get('package'), r.get('repository'), r.get('state')))
            if not verbose:
                break

        # openQA results
        for check in project.findall('missing_checks/*'):
            report.append('   - Missing check: ' + check.get('name'))

        for check in project.findall('checks/*'):
            state = check.find('state').text
            if state != 'success':
                info = "   - %s check: %s" % (state, check.get('name'))
                url = check.find('url')
                if url is not None:
                    info += " " + url.text
                report.append(info)
                break

        if project.get('state') == 'acceptable':
            report.insert(0, ' ++ Acceptable staging project %s' % project.get('name'))
        elif project.get('state') != 'empty':
            report.insert(0, ' -- %s Project %s still needs attention' % (project.get('state').upper(),
                                                                          project.get('name')))

        return report

    def _check_project(self, project):
        """
        Check state of one specified staging project
        :param project: project to check

        """
        info = self.api.project_status(project)
        if info.get('state') == 'empty':
            return []
        return self._report(info, False) + ['']

    def perform(self, project):
        """
        Check one staging project verbosibly or all of them at once
        :param project: project to check, None for all
        """
        if project:
            report = self._check_project(project)
        else:
            report = []
            for project in self.api.get_staging_projects():
                report.extend(self._check_project(project))

        print('\n'.join(report))

        return True
