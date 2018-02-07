from osclib.core import project_list_prefix


def project_list_family(apiurl, project):
    """
    Determine the available projects within the same product family.

    Skips < SLE-12 due to format change.
    """
    if project == 'openSUSE:Factory':
        return [project]

    count_original = project.count(':')
    if project.startswith('SUSE:SLE'):
        project = ':'.join(project.split(':')[:2])
        family_filter = lambda p: p.endswith(':GA') and not p.startswith('SUSE:SLE-11')
    else:
        family_filter = lambda p: p.count(':') == count_original

    prefix = ':'.join(project.split(':')[:-1])
    projects = project_list_prefix(apiurl, prefix)

    return filter(family_filter, projects)

def project_list_family_prior(apiurl, project, include_self=False):
    """
    Determine the available projects within the same product family released
    prior to the specified project.
    """
    projects = project_list_family(apiurl, project)
    past = False
    prior = []
    for entry in sorted(projects, key=project_list_family_sorter, reverse=True):
        if entry == project:
            past = True
            if not include_self:
                continue

        if past:
            prior.append(entry)

    return prior

def project_list_family_sorter(project):
    """Extract key to be used as sorter (oldest to newest)."""
    version = project_version(project)

    if version >= 42:
        version -= 42

    if project.endswith(':Update'):
        version += 0.01

    return version

def project_version(project):
    """
    Extract a float representation of the project version.

    For example:
    - openSUSE:Leap:15.0 -> 15.0
    - openSUSE:Leap:42.3 -> 42.3
    - SUSE:SLE-15:GA     -> 15.0
    - SUSE:SLE-15-SP1:GA -> 15.1
    """
    if ':Leap:' in project:
        return float(project.split(':')[2])

    if ':SLE-' in project:
        version = project.split(':')[1]
        parts = version.split('-')
        version = float(parts[1])
        if len(parts) > 2:
            # Add each service pack as a tenth.
            version += float(parts[2][2:]) / 10
        return version

    return None
