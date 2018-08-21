from osc import conf
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
        family_filter = lambda p: p.count(':') == count_original and p.endswith(':GA')
    else:
        family_filter = lambda p: p.count(':') == count_original

    prefix = ':'.join(project.split(':')[:-1])
    projects = project_list_prefix(apiurl, prefix)

    return filter(family_filter, projects)

def project_list_family_prior(apiurl, project, include_self=False, last=None):
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

        if entry == last:
            break

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

def mail_send(project, to, subject, body, from_key='maintainer', followup_to_key='release-list', dry=False):
    from email.mime.text import MIMEText
    import email.utils
    import smtplib

    config = conf.config[project]
    msg = MIMEText(body)
    msg['Message-ID'] = email.utils.make_msgid()
    msg['Date'] = email.utils.formatdate(localtime=1)
    msg['From'] = config['mail-{}'.format(from_key)]
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
