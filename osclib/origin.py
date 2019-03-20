from copy import deepcopy
from collections import namedtuple
import logging
from osc.core import get_request_list
from osclib.conf import Config
from osclib.core import attribute_value_load
from osclib.core import devel_project_get
from osclib.core import entity_exists
from osclib.core import package_source_hash
from osclib.core import package_source_hash_history
from osclib.core import project_remote_apiurl
from osclib.core import review_find_last
from osclib.core import reviews_remaining
from osclib.core import request_remote_identifier
from osclib.memoize import memoize
from osclib.util import project_list_family
from osclib.util import project_list_family_prior_pattern
import re
import yaml

NAME = 'origin-manager'
DEFAULTS = {
    'unknown_origin_wait': False,
    'origins': [],
    'review-user': '<config:origin-manager-review-user>',
    'fallback-group': '<config:origin-manager-fallback-group>',
    'fallback-workaround': {},
}
POLICY_DEFAULTS = {
    'additional_reviews': [],
    'automatic_updates': True,
    'maintainer_review_always': False,
    'maintainer_review_initial': True,
    'pending_submission_allow': False,
    'pending_submission_consider': False,
    'pending_submission_allowed_reviews': [
        '<config_source:staging>*',
        '<config_source:repo-checker>',
    ],
}

OriginInfo = namedtuple('OriginInfo', ['project', 'pending'])
PendingRequestInfo = namedtuple('PendingRequestInfo', ['identifier', 'reviews_remaining'])
PolicyResult = namedtuple('PolicyResult', ['wait', 'accept', 'reviews', 'comments'])

def origin_info_str(self):
    return self.project + ('+' if self.pending else '')
OriginInfo.__str__ = origin_info_str

@memoize(session=True)
def config_load(apiurl, project):
    config = attribute_value_load(apiurl, project, 'OriginConfig')
    if not config:
        return {}

    return config_resolve(apiurl, project, yaml.safe_load(config))

def config_origin_generator(origins, apiurl=None, project=None, package=None, skip_workarounds=False):
    for origin_item in origins:
        for origin, values in origin_item.items():
            is_workaround = origin_workaround_check(origin)
            if skip_workarounds and is_workaround:
                break

            if (origin == '<devel>' or origin == '<devel>~') and apiurl and project and package:
                devel_project, devel_package = devel_project_get(apiurl, project, package)
                if not devel_project:
                    break
                origin = devel_project
                if is_workaround:
                    origin = origin_workaround_ensure(origin)

            yield origin, values
            break # Only support single value inside list item.

def config_resolve(apiurl, project, config):
    defaults = POLICY_DEFAULTS.copy()
    defaults_workarounds = POLICY_DEFAULTS.copy()

    origins_original = config_origin_list(config)

    config_project = Config.get(apiurl, project)
    config_resolve_variables(config, config_project)

    origins = config['origins']
    i = 0
    while i < len(origins):
        origin = origins[i].keys()[0]
        values = origins[i][origin]

        if origin == '*':
            del origins[i]
            defaults.update(values)
            defaults_workarounds.update(values)
            config_resolve_apply(config, values, until='*')
        elif origin == '*~':
            del origins[i]
            defaults_workarounds.update(values)
            config_resolve_create_workarounds(config, values, origins_original)
            config_resolve_apply(config, values, workaround=True, until='*~')
        elif '*' in origin:
            # Does not allow for family + workaround expansion (ie. foo*~).
            del origins[i]
            config_resolve_create_family(apiurl, project, config, i, origin, values)
        elif origin.endswith('~'):
            values_new = deepcopy(defaults_workarounds)
            values_new.update(values)
            values.update(values_new)
            i += 1
        else:
            values_new = deepcopy(defaults)
            values_new.update(values)
            values.update(values_new)
            i += 1

    return config

def config_resolve_variables(config, config_project):
    defaults_merged = DEFAULTS.copy()
    defaults_merged.update(config)
    config.update(defaults_merged)

    for key in ['review-user', 'fallback-group']:
        config[key] = config_resolve_variable(config[key], config_project)

    if not config['review-user']:
        config['review-user'] = NAME

    for origin, values in config_origin_generator(config['origins']):
        if 'additional_reviews' in values:
            values['additional_reviews'] = [
                config_resolve_variable(v, config_project) for v in values['additional_reviews']]

def config_resolve_variable(value, config_project, key='config'):
    prefix = '<{}:'.format(key)
    end = value.rfind('>')
    if not value.startswith(prefix) or end == -1:
        return value

    key = value[len(prefix):end]
    if key in config_project and config_project[key]:
        return config_project[key] + value[end + 1:]
    return ''

def config_origin_list(config, apiurl=None, project=None, package=None, skip_workarounds=False):
    origin_list = []
    for origin, values in config_origin_generator(
        config['origins'], apiurl, project, package, skip_workarounds):
        origin_list.append(origin)
    return origin_list

def config_resolve_create_workarounds(config, values_workaround, origins_skip):
    origins = config['origins']
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
    projects = project_list_family_prior_pattern(apiurl, origin, project)
    for origin_expanded in reversed(projects):
        config['origins'].insert(position, { str(origin_expanded): values })

def config_resolve_apply(config, values_apply, key=None, workaround=False, until=None):
    for origin, values in config_origin_generator(config['origins']):
        if workaround and (not origin.endswith('~') or origin == '*~'):
            continue

        if key:
            if origin == key:
                values.update(values)
            continue

        if until and origin == until:
            break

        values.update(values_apply)

def origin_workaround_check(origin):
    return origin.endswith('~')

def origin_workaround_ensure(origin):
    if not origin_workaround_check(origin):
        return origin + '~'
    return origin

@memoize(session=True)
def origin_find(apiurl, target_project, package, source_hash=None, current=False,
                pending_allow=True, fallback=True):
    config = config_load(apiurl, target_project)

    if not source_hash:
        current = True
        source_hash = package_source_hash(apiurl, target_project, package)
        if not source_hash:
            return None

    logging.debug('origin_find: {}/{} with source {} ({}, {}, {})'.format(
        target_project, package, source_hash, current, pending_allow, fallback))

    for origin, values in config_origin_generator(config['origins'], apiurl, target_project, package, True):
        if project_source_contain(apiurl, origin, package, source_hash):
            return OriginInfo(origin, False)

        if pending_allow and (values['pending_submission_allow'] or values['pending_submission_consider']):
            pending = project_source_pending(apiurl, origin, package, source_hash)
            if pending is not False:
                return OriginInfo(origin, pending)

    if not fallback:
        return None

    # Unable to find matching origin, if current fallback to last known origin
    # and mark as workaround, otherwise return current origin as workaround.
    if current:
        origin_info = origin_find_fallback(apiurl, target_project, package, source_hash, config['review-user'])
    else:
        origin_info = origin_find(apiurl, target_project, package)

    if origin_info:
        # Force origin to be workaround since required fallback.
        origin = origin_workaround_ensure(origin_info.project)
        if origin in config_origin_list(config, apiurl, target_project, package):
            return OriginInfo(origin, origin_info.pending)

    return None

def project_source_contain(apiurl, project, package, source_hash):
    for source_hash_consider in package_source_hash_history(apiurl, project, package):
        project_source_log('contain', project, source_hash_consider, source_hash)
        if source_hash_consider == source_hash:
            return True

    return False

def project_source_pending(apiurl, project, package, source_hash):
    apiurl_remote, project_remote = project_remote_apiurl(apiurl, project)
    requests = get_request_list(apiurl_remote, project_remote, package, None, ['new', 'review'], 'submit')
    for request in requests:
        for action in request.actions:
            source_hash_consider = package_source_hash(
                apiurl_remote, action.src_project, action.src_package, action.src_rev)

            project_source_log('pending', project, source_hash_consider, source_hash)
            if source_hash_consider == source_hash:
                return PendingRequestInfo(
                    request_remote_identifier(apiurl, apiurl_remote, request.reqid),
                    reviews_remaining(request))

    return False

def project_source_log(key, project, source_hash_consider, source_hash):
    logging.debug('source_{}: {:<40} {} == {}{}'.format(
        key, project, source_hash_consider, source_hash,
        ' (match)' if source_hash_consider == source_hash else ''))

def origin_find_fallback(apiurl, target_project, package, source_hash, user):
    # Search accepted requests (newest to oldest), find the last review made by
    # the specified user, load comment as annotation, and extract origin.
    requests = get_request_list(apiurl, target_project, package, None, ['accepted'], 'submit')
    for request in sorted(requests, key=lambda r: r.reqid, reverse=True):
        review = review_find_last(request, user)
        if not review:
            continue

        annotation = origin_annotation_load(review.comment)
        return OriginInfo(annotation.get('origin'), False)

    # Fallback to searching workaround project.
    fallback_workaround = config_load(apiurl, target_project).get('fallback-workaround')
    if fallback_workaround:
        if project_source_contain(apiurl, fallback_workaround['project'], package, source_hash):
            return OriginInfo(fallback_workaround['origin'], False)

    # Attempt to find a revision of target package that matches an origin.
    first = True
    for source_hash_consider in package_source_hash_history(apiurl, target_project, package):
        if first:
            first = False
            continue

        origin_info = origin_find(
            apiurl, target_project, package, source_hash_consider, pending_allow=False, fallback=False)
        if origin_info:
            return origin_info

    return None

def origin_annotation_dump(origin_info_new, origin_info_old, override=False):
    data = {'origin': str(origin_info_new.project)}
    if origin_info_old and origin_info_new.project != origin_info_old.project:
        data['origin_old'] = str(origin_info_old.project)

    if override:
        data['origin'] = origin_workaround_ensure(data['origin'])
        data['comment'] = override

    return yaml.dump(data, default_flow_style=False)

def origin_annotation_load(annotation):
    # For some reason OBS insists on indenting every subsequent line which
    # screws up yaml parsing since indentation has meaning.
    return yaml.safe_load(re.sub(r'^\s+', '', annotation, flags=re.MULTILINE))

def origin_find_highest(apiurl, project, package):
    config = config_load(apiurl, project)
    for origin, values in config_origin_generator(config['origins'], apiurl, project, package, True):
        if entity_exists(apiurl, origin, package):
            return origin

    return None

def policy_evaluate(apiurl, project, package,
                    origin_info_new, origin_info_old,
                    source_hash_new, source_hash_old):
    if origin_info_new is None:
        config = config_load(apiurl, project)
        origins = config_origin_list(config, apiurl, project, package, True)
        comment = 'Source not found in allowed origins:\n\n- {}'.format('\n- '.join(origins))
        return PolicyResult(config['unknown_origin_wait'], False, {}, [comment])

    policy = policy_get(apiurl, project, package, origin_info_new.project)
    inputs = policy_input_calculate(apiurl, project, package,
                                    origin_info_new, origin_info_old,
                                    source_hash_new, source_hash_old)
    result = policy_input_evaluate(policy, inputs)

    inputs['pending_submission'] = str(inputs['pending_submission'])
    logging.debug('policy_evaluate:\n\n{}'.format('\n'.join([
        '# policy\n{}'.format(yaml.dump(policy, default_flow_style=False)),
        '# inputs\n{}'.format(yaml.dump(inputs, default_flow_style=False)),
        str(result)])))
    return result

@memoize(session=True)
def policy_get(apiurl, project, package, origin):
    config = config_load(apiurl, project)
    for key, values in config_origin_generator(config['origins'], apiurl, project, package):
        if key == origin:
            return policy_get_preprocess(apiurl, origin, values)

    return None

def policy_get_preprocess(apiurl, origin, policy):
    project = origin.rstrip('~')
    config_project = Config.get(apiurl, project)
    policy['pending_submission_allowed_reviews'] = filter(None, [
        config_resolve_variable(v, config_project, 'config_source')
        for v in policy['pending_submission_allowed_reviews']])

    return policy

def policy_input_calculate(apiurl, project, package,
                           origin_info_new, origin_info_old,
                           source_hash_new, source_hash_old):
    inputs = {
        # Treat no older origin info as new package.
        'new_package': not entity_exists(apiurl, project, package) or origin_info_old is None,
        'pending_submission': origin_info_new.pending,
    }

    if inputs['new_package']:
        origin_highest = origin_find_highest(apiurl, project, package)
        inputs['from_highest_priority'] = \
            origin_highest is None or origin_info_new.project == origin_highest
    else:
        workaround_new = origin_workaround_check(origin_info_new.project)
        inputs['origin_change'] = origin_info_new.project != origin_info_old.project
        if inputs['origin_change']:
            config = config_load(apiurl, project)
            origins = config_origin_list(config, apiurl, project, package)

            inputs['higher_priority'] = \
                origins.index(origin_info_new.project) < origins.index(origin_info_old.project)
            if workaround_new:
                inputs['same_family'] = True
            else:
                inputs['same_family'] = \
                    origin_info_new.project in project_list_family(
                        apiurl, origin_info_old.project.rstrip('~'), True)
        else:
            inputs['higher_priority'] = None
            inputs['same_family'] = True

        if inputs['pending_submission']:
            inputs['direction'] = 'forward'
        else:
            if workaround_new:
                source_hashes = []
            else:
                source_hashes = list(package_source_hash_history(
                    apiurl, origin_info_new.project, package, 10, True))

            try:
                index_new = source_hashes.index(source_hash_new)
                index_old = source_hashes.index(source_hash_old)
                if index_new == index_old:
                    inputs['direction'] = 'none'
                else:
                    inputs['direction'] = 'forward' if index_new < index_old else 'backward'
            except ValueError:
                inputs['direction'] = 'unkown'

    return inputs

def policy_input_evaluate(policy, inputs):
    result = PolicyResult(False, True, {}, [])

    if inputs['new_package']:
        if policy['maintainer_review_initial']:
            result.reviews['maintainer'] = 'Need package maintainer approval for inital submission.'

        if not inputs['from_highest_priority']:
            result.reviews['fallback'] = 'Not from the highest priority origin which provides the package.'
    else:
        if inputs['direction'] == 'none':
            return PolicyResult(False, False, {}, ['Identical source.'])

        if inputs['origin_change']:
            if inputs['higher_priority']:
                if not inputs['same_family'] and inputs['direction'] != 'forward':
                    result.reviews['fallback'] = 'Changing to a higher priority origin, ' \
                        'but from another family and {} direction.'.format(inputs['direction'])
                elif not inputs['same_family']:
                    result.reviews['fallback'] = 'Changing to a higher priority origin, but from another family.'
                elif inputs['direction'] != 'forward':
                    result.reviews['fallback'] = \
                        'Changing to a higher priority origin, but {} direction.'.format(inputs['direction'])
            else:
                result.reviews['fallback'] = 'Changing to a lower priority origin.'
        else:
            if inputs['direction'] == 'forward':
                if not policy['automatic_updates']:
                    result.reviews['fallback'] = 'Forward direction, but automatic updates not allowed.'
            else:
                result.reviews['fallback'] = '{} direction.'.format(inputs['direction'])

    if inputs['pending_submission'] is not False:
        reviews_not_allowed = policy_input_evaluate_reviews_not_allowed(policy, inputs)
        wait = not policy['pending_submission_allow'] or len(reviews_not_allowed)
        result = PolicyResult(wait, True, result.reviews, result.comments)

        if wait:
            result.comments.append('Waiting on {} of {}.'.format(
                'reviews' if policy['pending_submission_allow'] else 'acceptance',
                inputs['pending_submission'].identifier))

    if policy['maintainer_review_always']:
        # Placed last to override initial maintainer approval message.
        result.reviews['maintainer'] = 'Need package maintainer approval.'

    for additional_review in policy['additional_reviews']:
        if additional_review not in result.reviews:
            result.reviews[additional_review] = 'Additional review required based on origin.'

    return result

def policy_input_evaluate_reviews_not_allowed(policy, inputs):
    reviews_not_allowed = []
    for review_remaining in inputs['pending_submission'].reviews_remaining:
        allowed = False
        for review_allowed in policy['pending_submission_allowed_reviews']:
            if review_allowed.endswith('*') and review_remaining.startswith(review_allowed[:-1]):
                allowed = True
                break
            if review_remaining == review_allowed:
                allowed = True
                break

        if not allowed:
            reviews_not_allowed.append(review_remaining)

    return reviews_not_allowed
