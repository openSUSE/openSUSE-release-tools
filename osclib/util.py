import logging

from osc import conf
from osclib.conf import Config
from osclib.core import entity_email
from osclib.core import project_list_prefix
from osclib.memoize import memoize

logger = logging.getLogger()


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

    def filter_sle_updates(p):
        return p.count(':') == count_original and (p.endswith(':GA') or (include_update and p.endswith(':Update')))

    def filter_opensuse_updates(p):
        return p.count(':') == count_original or (include_update and p.count(':') == count_original + 1 and p.endswith(':Update'))

    if project.startswith('SUSE:SLE'):
        project = ':'.join(project.split(':')[:2])

        family_filter = filter_sle_updates
    else:
        family_filter = filter_opensuse_updates

    prefix = ':'.join(project.split(':')[:-1])
    projects = project_list_prefix(apiurl, prefix)
    projects = list(filter(family_filter, projects))

    if project_suffix:
        for i, project in enumerate(projects):
            if project.endswith(':Update'):
                projects[i] = project.replace(':Update', project_suffix + ':Update')
            else:
                projects[i] += project_suffix

    return projects


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


def mail_send_with_details(relay, sender, subject, to, text, xmailer=None, followup_to=None, dry=True):
    import smtplib
    from email.mime.text import MIMEText
    import email.utils
    msg = MIMEText(text, _charset='UTF-8')
    msg['Subject'] = subject
    msg['Message-ID'] = email.utils.make_msgid()
    msg['Date'] = email.utils.formatdate(localtime=1)
    msg['From'] = sender
    msg['To'] = to
    if followup_to:
        msg['Mail-Followup-To'] = followup_to
    if xmailer:
        msg.add_header('X-Mailer', xmailer)
    msg.add_header('Precedence', 'bulk')
    if dry:
        logger.debug(text)
        logger.debug(msg.as_string())
        return
    logger.info("%s: %s", msg['To'], msg['Subject'])
    s = smtplib.SMTP(relay)
    s.sendmail(msg['From'], {msg['To'], sender}, msg.as_string())
    s.quit()


def mail_send(apiurl, project, to, subject, body, from_key='maintainer',
              followup_to_key='release-list', dry=False):

    config = Config.get(apiurl, project)
    if from_key is None:
        sender = entity_email(apiurl, conf.get_apiurl_usr(apiurl), include_name=True)
    else:
        sender = config[f'mail-{from_key}']

    if '@' not in to:
        to = config[f'mail-{to}']

    followup_to = config.get(f'mail-{followup_to_key}')
    relay = config.get('mail-relay', 'relay.suse.de')

    mail_send_with_details(text=body, subject=subject, relay=relay, sender=sender,
                           followup_to=followup_to, to=to, dry=dry)


def sha1_short(data):
    import hashlib

    if isinstance(data, list):
        data = '::'.join(data)

    if isinstance(data, str):
        data = data.encode('utf-8')

    return hashlib.sha1(data).hexdigest()[:7]


def rmtree_nfs_safe(path, attempts=5):
    import shutil
    try:
        shutil.rmtree(path)
    except OSError as e:
        # Directory not empty due to slow filesystem (see #1326 old occurance).
        if attempts <= 0 or e.errno != 39:
            raise e

        from time import sleep
        sleep(0.25)

        rmtree_nfs_safe(path, attempts - 1)
