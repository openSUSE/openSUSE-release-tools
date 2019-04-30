from osc import conf
from osclib.conf import Config
from osclib.core import entity_email
from osclib.core import project_list_prefix
from osclib.memoize import memoize


@memoize(session=True)
def project_list_family(apiurl, project, include_update=False):
    """
    Determine the available projects within the same product family.

    Skips < SLE-12 due to format change.
    """
    if project.endswith(':NonFree'):
        project = project[:-8]
        project_suffix = ':NonFree'
    else:
        project_suffix = ''

    if project == 'openSUSE:Factory':
        return [project + project_suffix]

    if project.endswith(':ARM') or project.endswith(':PowerPC'):
        return [project + project_suffix]

    count_original = project.count(':')
    if project.startswith('SUSE:SLE'):
        project = ':'.join(project.split(':')[:2])
        family_filter = lambda p: p.count(':') == count_original and (
            p.endswith(':GA') or (include_update and p.endswith(':Update')))
    else:
        family_filter = lambda p: p.count(':') == count_original or (
            include_update and p.count(':') == count_original + 1 and p.endswith(':Update'))

    prefix = ':'.join(project.split(':')[:-1])
    projects = project_list_prefix(apiurl, prefix)
    projects = filter(family_filter, projects)

    if project_suffix:
        for i, project in enumerate(projects):
            if project.endswith(':Update'):
                projects[i] = project.replace(':Update', project_suffix + ':Update')
            else:
                projects[i] += project_suffix

    return list(projects)

def project_list_family_prior(apiurl, project, include_self=False, last=None, include_update=False):
    """
    Determine the available projects within the same product family released
    prior to the specified project.
    """
    projects = project_list_family(apiurl, project, include_update)
    past = False
    prior = []
    for entry in sorted(projects, key=project_list_family_sorter, reverse=True):
        if entry == project:
            past = True
            if not include_self:
                continue

        if past:
            prior.append(entry)

        if entry == last:
            break

    return prior

def project_list_family_prior_pattern(apiurl, project_pattern, project=None, include_update=True):
    project_prefix, project_suffix = project_pattern.split('*', 2)
    if project:
        project = project if project.startswith(project_prefix) else None

    if project:
        projects = project_list_family_prior(apiurl, project, include_update=include_update)
    else:
        if ':Leap:' in project_prefix:
            project = project_prefix

        if ':SLE-' in project_prefix:
            project = project_prefix + ':GA'

        projects = project_list_family(apiurl, project, include_update)
        projects = sorted(projects, key=project_list_family_sorter, reverse=True)

    return [p for p in projects if p.startswith(project_prefix)]

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

    return 0

def mail_send(apiurl, project, to, subject, body, from_key='maintainer',
              followup_to_key='release-list', dry=False):
    from email.mime.text import MIMEText
    import email.utils
    import smtplib

    config = Config.get(apiurl, project)
    msg = MIMEText(body)
    msg['Message-ID'] = email.utils.make_msgid()
    msg['Date'] = email.utils.formatdate(localtime=1)
    if from_key is None:
        msg['From'] = entity_email(apiurl, conf.get_apiurl_usr(apiurl), include_name=True)
    else:
        msg['From'] = config['mail-{}'.format(from_key)]
    if '@' not in to:
        to = config['mail-{}'.format(to)]
    msg['To'] = to
    followup_to = config.get('mail-{}'.format(followup_to_key))
    if followup_to:
        msg['Mail-Followup-To'] = followup_to
    msg['Subject'] = subject

    if dry:
        print(msg.as_string())
        return

    s = smtplib.SMTP(config.get('mail-relay', 'relay.suse.de'))
    s.sendmail(msg['From'], [msg['To']], msg.as_string())
    s.quit()

def sha1_short(data):
    import hashlib

    if isinstance(data, list):
        data = '::'.join(data)

    return hashlib.sha1(data).hexdigest()[:7]
