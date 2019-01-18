from copy import deepcopy
from lxml import etree as ET
from osc.core import get_commitlog
from osc.core import get_request_list
from osc.core import HTTPError
from osclib.conf import Config
from osclib.core import attribute_value_load
from osclib.core import devel_project_get
from osclib.core import project_source_info
from osclib.memoize import memoize
from osclib.util import project_list_family_prior_prefix
import yaml

POLICY_DEFAULTS = {
    'additional_reviews': [],
    'automatic_updates': True,
    'maintainer_review': True,
    'pending_submission_allow': False,
    'pending_submission_consider': False,
}

@memoize(session=True)
def config_load(apiurl, project):
    config = attribute_value_load(apiurl, project, 'OriginConfig')
    if not config:
        return None

    return config_resolve(apiurl, project, yaml.safe_load(config))

def config_origin_generator(origins):
    for origin_item in origins:
        for origin, values in origin_item.items():
            yield origin, values
            break # Only support single value inside list item.

def config_resolve(apiurl, project, config):
    defaults = POLICY_DEFAULTS.copy()
    defaults_workarounds = POLICY_DEFAULTS.copy()

    origins_original = config_origin_list(config)

    config_project = Config.get(apiurl, project)
    config_resolve_variables(config, config_project)

    origins = config.get('origins', [])
    i = 0
    for origin, values in config_origin_generator(origins):
        if origin == '*':
            del origins[i]
            defaults.update(values)
            config_resolve_apply(config, values, until='*')
        elif origin == '*~':
            del origins[i]
            defaults_workarounds.update(values)
            config_resolve_create_workarounds(config, values, origins_original)
            config_resolve_apply(config, values, workaround=True, until='*~')
        elif origin.endswith('*'):
            # Does not allow for family + workaround expansion (ie. foo*~).
            del origins[i]
            config_resolve_create_family(apiurl, project, config, i, origin, values)
        elif origin.endswith('~'):
            values_new = deepcopy(defaults_workarounds)
            values_new.update(values)
            values.update(values_new)
        else:
            values_new = deepcopy(defaults)
            values_new.update(values)
            values.update(values_new)

        i += 1

    return config

def config_resolve_variables(config, config_project):
    for origin, values in config_origin_generator(config.get('origins', [])):
        if 'additional_reviews' in values:
            values['additional_reviews'] = [
                config_resolve_variable(v, config_project) for v in values['additional_reviews']]

def config_resolve_variable(value, config_project):
    if not value.startswith('<config:'):
        return value

    key = value[8:-1]
    return config_project.get(key, '')

def config_origin_list(config):
    origin_list = []
    for origin, values in config_origin_generator(config.get('origins', [])):
        origin_list.append(origin)
    return origin_list

def config_resolve_create_workarounds(config, values_workaround, origins_skip):
    origins = config.get('origins', [])
    i = 0
    for origin, values in config_origin_generator(origins):
        i += 1
        if origin.startswith('*') or origin.endswith('~'):
            continue

        origin_new = origin + '~'
        if origin_new in origins_skip:
            continue

        values_new = deepcopy(values)
        values_new.update(values_workaround)
        origins.insert(i, { origin_new: values_new })

def config_resolve_create_family(apiurl, project, config, position, origin, values):
    origins = config.get('origins', [])

    project_prefix = origin[:-1]
    project_for_family = project if project.startswith(project_prefix) else None
    projects = project_list_family_prior_prefix(apiurl, project_prefix, project_for_family)
    for origin_expanded in reversed(projects):
        origins.insert(position, { origin_expanded: values })

def config_resolve_apply(config, values_apply, key=None, workaround=False, until=None):
    for origin, values in config_origin_generator(config.get('origins', [])):
        if workaround and (not origin.endswith('~') or origin == '*~'):
            continue

        if key:
            if origin == key:
                values.update(values)
            continue

        if until and origin == until:
            break

        values.update(values_apply)

def origin_find(apiurl, target_project, package, srcmd5):
    config = config_load(apiurl, target_project)

    for origin, values in config_origin_generator(config.get('origins', [])):
        if origin.endswith('~'):
            continue

        # Devel project can only be evaluated given a package context.
        if origin == '<devel>':
            devel_project, devel_package = devel_project_get(apiurl, target_project, package)
            if not devel_project:
                continue
            origin = devel_project

        print('considering', origin, package, srcmd5)

        if project_source_contain(apiurl, origin, package, srcmd5):
            return origin, True

        if values['pending_submission_allow'] or values['pending_submission_consider']:
            # TODO Check for pending requests and indicate if that is source
            pending = project_source_pending(apiurl, origin, package, srcmd5)
            if pending:
                if values['pending_submission_allow']:
                    return origin, True
                if values['pending_submission_consider']:
                    return origin, None

    # TODO loop through non-workaround origins
    # - if no match found
    #     look for last request having annotation and utilize
    #     use current origin (target project) and classify as workaround for that

    # TODO handle self origin for SLE (and rule to allow)
    # TODO handle package from new devel
    return None, None

def project_source_contain(apiurl, project, package, srcmd5, aggregate=False):
    # Check if package exists with project and if first reivions..
    # perhaps only do this stage if doing report
    if aggregate:
        # When performing an aggregate check (for reports and such) attempt
        # looking a project level overview which is cached and thus cheaper.
        source_info = project_source_info(apiurl, project, package, aggregate)
        if not source_info:
            # Package is not available in project regardless of srcmd5.
            return False

        if source_info.get('srcmd5') == srcmd5:
            return True

    # Fallback to searching package revision history.
    try:
        root = ET.fromstringlist(
            get_commitlog(apiurl, project, package, None, format='xml'))
    except HTTPError as e:
        if e.code == 404:
            # Presumably not aggregate and package not in project at all.
            return False

        raise e

    for entry in root.findall('logentry'):
        print(entry.get('srcmd5'))
        if entry.get('srcmd5') == srcmd5:
            return True

    return False

def project_source_pending(apiurl, project, package, srcmd5):
    # TODO use query for remote projects to build map instead of hard-coded like
    # ReviewBot
    #('openSUSE.org:', 'https://api.opensuse.org', 'obsrq'),
    requests = get_request_list(apiurl, project, package, None, ['new', 'review'], 'submit')
    for request in requests:
        for action in request.actions:
            # TODO Move method to osclib.core.
            # TODO Handle remote project prefix reattach.
            import ReviewBot
            source_info = ReviewBot._get_sourceinfo(apiurl, action.src_project, action.src_package, action.src_rev)

            if source_info.get('srcmd5') == srcmd5:
                # TODO handle review state see FactorySourceChecker._check_requests()
                return True

    return False
