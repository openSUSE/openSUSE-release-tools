from copy import deepcopy
from collections import namedtuple
import logging
from osc.core import get_request_list
from osclib.conf import Config
from osclib.conf import str2bool
from osclib.core import attribute_value_load
from osclib.core import devel_project_get
from osclib.core import devel_projects
from osclib.core import entity_exists
from osclib.core import package_source_age
from osclib.core import package_source_hash
from osclib.core import package_source_hash_history
from osclib.core import package_version
from osclib.core import project_attributes_list
from osclib.core import project_remote_apiurl
from osclib.core import project_remote_prefixed
from osclib.core import request_action_key
from osclib.core import request_action_list
from osclib.core import request_action_list_source
from osclib.core import request_create_change_devel
from osclib.core import request_create_delete
from osclib.core import request_create_submit
from osclib.core import request_remote_identifier
from osclib.core import review_find_last
from osclib.core import reviews_remaining
from osclib.memoize import memoize
from osclib.memoize import memoize_session_reset
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
    'automatic_updates_initial': False,
    'automatic_updates_supersede': True,
    'automatic_updates_delay': 0,
    'automatic_updates_frequency': 0,
    'maintainer_review_always': False,
    'maintainer_review_initial': True,
    'pending_submission_allow': False,
    'pending_submission_consider': False,
    'pending_submission_allowed_reviews': [
        # Non-maintenance projects:
        '<config_source:staging>*',
        # Maintenance projects:
        '<config_source:review-install-check>',
        '<config_source:review-openqa>',
    ],
    # Submit pending requests with a set of allowed reviews, but still wait for
    # the above reviews before being accepted.
    'pending_submission_allowed_reviews_update': [
        '!maintenance_incident',
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
                devel_project, devel_package = origin_devel_project(apiurl, project, package)
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
        origin = next(iter(origins[i]))
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

def origin_workaround_strip(origin):
    if origin_workaround_check(origin):
        return origin[:-1]
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
    for source_hash_consider in package_source_hash_history(
        apiurl, project, package, include_project_link=True):
        project_source_log('contain', project, source_hash_consider, source_hash)
        if source_hash_consider == source_hash:
            return True

    return False

def project_source_pending(apiurl, project, package, source_hash):
    apiurl_remote, project_remote = project_remote_apiurl(apiurl, project)
    request_actions = request_action_list_source(apiurl_remote, project_remote, package,
                                                 states=['new', 'review'], include_release=True)
    for request, action in request_actions:
        source_hash_consider = package_source_hash(
            apiurl_remote, action.src_project, action.src_package, action.src_rev)

        project_source_log('pending', project, source_hash_consider, source_hash)
        if source_hash_consider == source_hash:
            return PendingRequestInfo(
                request_remote_identifier(apiurl, apiurl_remote, request.reqid),
                reviews_remaining(request, True))

    return False

def project_source_log(key, project, source_hash_consider, source_hash):
    logging.debug('source_{}: {:<40} {} == {}{}'.format(
        key, project, source_hash_consider, source_hash,
        ' (match)' if source_hash_consider == source_hash else ''))

def origin_find_fallback(apiurl, target_project, package, source_hash, user):
    # Search accepted requests (newest to oldest), find the last review made by
    # the specified user, load comment as annotation, and extract origin.
    request_actions = request_action_list_source(apiurl, target_project, package, states=['accepted'])
    for request, action in sorted(request_actions, key=lambda i: i[0].reqid, reverse=True):
        annotation = origin_annotation_load(request, action, user)
        if not annotation:
            continue

        return OriginInfo(annotation.get('origin'), False)

    # Fallback to searching workaround project.
    fallback_workaround = config_load(apiurl, target_project).get('fallback-workaround')
    if fallback_workaround:
        if project_source_contain(apiurl, fallback_workaround['project'], package, source_hash):
            return OriginInfo(fallback_workaround['origin'], False)

    # Attempt to find a revision of target package that matches an origin.
    first = True
    for source_hash_consider in package_source_hash_history(
        apiurl, target_project, package, include_project_link=True):
        if first:
            first = False
            continue

        origin_info = origin_find(
            apiurl, target_project, package, source_hash_consider, pending_allow=False, fallback=False)
        if origin_info:
            return origin_info

    return None

def origin_annotation_dump(origin_info_new, origin_info_old, override=False, raw=False):
    data = {}
    if origin_info_new is None:
        data['origin'] = str(origin_info_new)
    else:
        data['origin'] = str(origin_info_new.project)
        if origin_info_new.pending:
            data['pending'] = origin_info_new.pending.identifier

    if origin_info_old and origin_info_new.project != origin_info_old.project:
        data['origin_old'] = str(origin_info_old.project)

    if override:
        data['origin'] = origin_workaround_ensure(data['origin'])
        data['comment'] = override

    if raw:
        return data

    return yaml.dump(data, default_flow_style=False)

def origin_annotation_load(request, action, user):
    # Find last accepted review which means it was reviewed and annotated.
    review = review_find_last(request, user, ['accepted'])
    if not review:
        return False

    try:
        annotation = yaml.safe_load(review.comment)
    except yaml.scanner.ScannerError as e:
        # OBS used to prefix subsequent review lines with two spaces. At some
        # point it was changed to no longer indent, but still need to be able
        # to load older annotations.
        comment_stripped = re.sub(r'^  ', '', review.comment, flags=re.MULTILINE)
        annotation = yaml.safe_load(comment_stripped)

    if not annotation or type(annotation) is not dict or 'origin' not in annotation:
        # Only returned structured data (ie. dict) with a minimum of the origin
        # key available, otherwise None.
        return None

    if len(request.actions) > 1:
        action_key = request_action_key(action)
        if action_key not in annotation:
            return False

        return annotation[action_key]

    return annotation

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
    for suffix in ('', '_update'):
        key = 'pending_submission_allowed_reviews{}'.format(suffix)
        policy[key] = list(filter(None, [
            config_resolve_variable(v, config_project, 'config_source')
            for v in policy[key]]))

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

            if origin_info_old.project in origins:
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
                inputs['same_family'] = False
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
                inputs['direction'] = 'unknown'

    return inputs

def policy_input_evaluate(policy, inputs):
    result = PolicyResult(False, True, {}, [])

    if inputs['new_package']:
        if policy['maintainer_review_initial']:
            result.reviews['maintainer'] = 'Need package maintainer approval for initial submission.'

        if not inputs['from_highest_priority']:
            result.reviews['fallback'] = 'Not from the highest priority origin which provides the package.'
    else:
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
            if inputs['direction'] == 'none':
                return PolicyResult(False, False, {}, ['Identical source.'])

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
    return reviews_filter_allowed(inputs['pending_submission'].reviews_remaining,
                                  policy['pending_submission_allowed_reviews'])

def reviews_filter_allowed(reviews_remaining, allowed_reviews):
    reviews_not_allowed = []
    for review_remaining in reviews_remaining:
        allowed = None
        not_rule = False
        for review_allowed in allowed_reviews:
            result = True
            if review_allowed.startswith('!'):
                not_rule = True
                review_allowed = review_allowed[1:]
                result = False

            if review_allowed.endswith('*') and review_remaining.startswith(review_allowed[:-1]):
                allowed = result
                break
            if review_remaining == review_allowed:
                allowed = result
                break

        # If not the allowed case then add the review to not allowed list.
        # Allowed if either matches an allow rule or does not match anything
        # when at least one not rule is present.
        if not (allowed or (not_rule and allowed is None)):
            reviews_not_allowed.append(review_remaining)

    return reviews_not_allowed

def origin_revision_state(apiurl, target_project, package, origin_info=False, limit=10):
    if origin_info is False:
        origin_info = origin_find(apiurl, target_project, package)

    revisions = []

    # Allow for origin project to contain revisions not present in target by
    # considering double the limit of revisions. The goal is to know how many
    # revisions behind the package in target project is and if it deviated from
    # origin, not that it ended up with every revision found in origin project.
    if origin_info is None:
        origin_hashes = []
    else:
        origin_project = origin_info.project.rstrip('~')
        origin_hashes = list(package_source_hash_history(apiurl, origin_project, package, limit * 2, True))
    target_hashes = list(package_source_hash_history(apiurl, target_project, package, limit, True))
    for source_hash in origin_hashes:
        if source_hash not in target_hashes:
            revisions.append(-1)
        else:
            break

    for source_hash in target_hashes:
        if len(revisions) == limit:
            break

        revisions.append(int(source_hash in origin_hashes))

    # To simplify usage which is left-right (oldest-newest) place oldest first.
    return list(reversed(revisions))

def origin_potential(apiurl, target_project, package, require_update_initial=False):
    config = config_load(apiurl, target_project)
    for origin, _ in config_origin_generator(config['origins'], apiurl, target_project, package, True):
        version = package_version(apiurl, origin, package)
        if version is not False:
            if require_update_initial:
                policy = policy_get(apiurl, target_project, package, origin)
                if not policy['automatic_updates_initial']:
                    continue

            # Package exists in origin, but may still have unknown version.
            return origin, version

    return None, None

def origin_potentials(apiurl, target_project, package):
    potentials = {}

    config = config_load(apiurl, target_project)
    for origin, _ in config_origin_generator(config['origins'], apiurl, target_project, package, True):
        version = package_version(apiurl, origin, package)
        if version is not False:
            # Package exists in origin, but may still have unknown version.
            potentials[origin] = version

    return potentials

def origin_history(apiurl, target_project, package, user):
    history = []

    request_actions = request_action_list_source(apiurl, target_project, package, states=['all'])
    for request, action in sorted(request_actions, key=lambda i: i[0].reqid, reverse=True):
        annotation = origin_annotation_load(request, action, user)
        if not annotation:
            continue

        history.append({
            'origin': annotation.get('origin', 'None'),
            'request': request.reqid,
            'state': request.state.name,
            'source_project': action.src_project,
            'source_package': action.src_package,
            'source_revision': action.src_rev,
        })

    return history

def origin_update(apiurl, target_project, package):
    origin_info = origin_find(apiurl, target_project, package)
    if not origin_info:
        # Cases for a lack of origin:
        # - package does not exist in target_project
        # - initial package submission from devel (lacking devel meta on package)
        # - initial package submission overriden to allow from no origin
        # - origin project/package deleted
        #
        # Ideally, the second case should never be used and instead the first
        # case should be opted for instead.

        # Check for accepted source submission with devel annotation and create
        # change_devel request as automatic follow-up to approval.
        config = config_load(apiurl, target_project)
        request_actions = request_action_list_source(apiurl, target_project, package, states=['accepted'])
        for request, action in sorted(request_actions, key=lambda i: i[0].reqid, reverse=True):
            annotation = origin_annotation_load(request, action, config['review-user'])
            if not annotation:
                continue

            origin = annotation.get('origin')
            if origin_workaround_check(origin):
                continue

            if origin not in config_origin_list(config, apiurl, target_project):
                message = f'Set devel project based on initial submission in request#{request.reqid}.'
                return request_create_change_devel(apiurl, origin, package, target_project, message=message)

        # Either the first or one of the second two cases.
        exists = entity_exists(apiurl, target_project, package)
        origin, version = origin_potential(apiurl, target_project, package, not exists)
        if origin is None:
            if not exists:
                # Package does not exist to be deleted.
                return False

            # Package is not found in any origin so request deletion.
            message = 'Package not available from any potential origin.'
            return request_create_delete(apiurl, target_project, package, message)

        if not exists:
            message = 'Submitting new package from highest potential origin.'
            return request_create_submit(apiurl, origin, package, target_project, message=message,
                                         ignore_if_any_request=True)

        # No longer tracking previous origin (likely removed from origin) so
        # submit from the highest potential origin.
        message = 'Submitting package from highest potential origin.'
        return request_create_submit(apiurl, origin, package, target_project, message=message)

    if origin_workaround_check(origin_info.project):
        # Do not attempt to update workarounds as the expected flow is to either
        # to explicitely switched back to non-workaround or source to match at
        # some point and implicitily switch.
        return False

    if origin_info.pending:
        # Already accepted source ahead of origin so nothing to do.
        return False

    policy = policy_get(apiurl, target_project, package, origin_info.project)
    if not policy['automatic_updates']:
        return False

    mode = origin_update_mode(apiurl, target_project, package, policy, origin_info.project)
    if mode['skip']:
        return False

    age = package_source_age(apiurl, origin_info.project, package).total_seconds()
    if age < int(mode['delay']):
        return False

    supersede = str2bool(str(mode['supersede']))
    frequency = int(mode['frequency'])

    if policy['pending_submission_allow']:
        request_id = origin_update_pending(
            apiurl, origin_info.project, package, target_project, policy, supersede, frequency)
        if request_id:
            return request_id

    message = 'Newer source available from package origin.'
    return request_create_submit(apiurl, origin_info.project, package, target_project, message=message,
                                 supersede=supersede, frequency=frequency)

def origin_update_pending(apiurl, origin_project, package, target_project, policy, supersede, frequency):
    apiurl_remote, project_remote = project_remote_apiurl(apiurl, origin_project)
    request_actions = request_action_list_source(
        apiurl_remote, project_remote, package, include_release=True)
    for request, action in sorted(request_actions, key=lambda i: i[0].reqid, reverse=True):
        reviews_not_allowed = reviews_filter_allowed(
            reviews_remaining(request, True), policy['pending_submission_allowed_reviews_update'])
        if len(reviews_not_allowed):
            continue

        identifier = request_remote_identifier(apiurl, apiurl_remote, request.reqid)
        message = 'Newer pending source available from package origin. See {}.'.format(identifier)
        src_project = project_remote_prefixed(apiurl, apiurl_remote, action.src_project)
        return request_create_submit(apiurl, src_project, action.src_package,
                                     target_project, package, message=message, revision=action.src_rev,
                                     supersede=supersede, frequency=frequency)

    return False

def origin_update_mode(apiurl, target_project, package, policy, origin_project):
    values = {}
    for key in ('skip', 'supersede', 'delay', 'frequency'):
        attribute = 'OriginUpdate{}'.format(key.capitalize())
        for project in (origin_project, target_project):
            for package_attribute in (package, None):
                value = attribute_value_load(apiurl, project, attribute, package=package_attribute)
                if value is not None:
                    values[key] = value
                    break

            if key in values:
                break

        if key in values:
            continue

        if key == 'skip':
            values[key] = not policy['automatic_updates']
        else:
            values[key] = policy[f'automatic_updates_{key}']

    return values

@memoize(session=True)
def origin_updatable(apiurl):
    """ List of origin managed projects that can be updated. """
    projects = project_attributes_list(apiurl, [
        'OSRT:OriginConfig',
    ], [
        'OBS:Maintained', # Submitting maintenance updates not currently supported.
        'OSRT:OriginUpdateSkip',
    ], locked=False)

    for project in projects:
        updatable = False

        # Look for at least one origin that allows automatic updates.
        config = config_load(apiurl, project)
        for origin, values in config_origin_generator(config['origins'], skip_workarounds=True):
            if values['automatic_updates']:
                updatable = True
                break

        if not updatable:
            projects.remove(project)

    return projects

@memoize(session=True)
def origin_updatable_map(apiurl, pending=None, include_self=False):
    origins = {}
    for project in origin_updatable(apiurl):
        config = config_load(apiurl, project)
        for origin, values in config_origin_generator(config['origins'], skip_workarounds=True):
            if pending is not None and values['pending_submission_allow'] != pending:
                continue

            if origin == '<devel>':
                for devel in origin_devel_projects(apiurl, project):
                    origins.setdefault(devel, set())
                    origins[devel].add(project)
            else:
                origins.setdefault(origin, set())
                origins[origin].add(project)

        if include_self:
            origins.setdefault(project, set())
            origins[project].add(project)

    return origins

def origin_updatable_initial(apiurl, target_project):
    origins = []

    config = config_load(apiurl, target_project)
    for origin, values in config_origin_generator(config['origins'], skip_workarounds=True):
        if origin != '<devel>' and values['automatic_updates_initial']:
            origins.append(origin)

    return origins

class devel_project_simulate_exception(Exception):
    pass

class devel_project_simulate:
    lock = None

    def __init__(self, apiurl, target_project, target_package, devel_project, devel_package):
        self.apiurl = apiurl
        self.target_project = target_project
        self.target_package = target_package
        self.devel_project = devel_project
        self.devel_package = devel_package

    def get(self, apiurl, target_project, target_package):
        if (apiurl == self.apiurl and
            target_project == self.target_project and
            target_package == self.target_package):
            return self.devel_project, self.devel_package

        return False, False

    def __enter__(self):
        if devel_project_simulate.lock:
            raise devel_project_simulate_exception(
                'Devel project simulation lock already aquired for {}:{}/{}'.format(
                    self.apiurl, self.target_project, self.target_package))

        devel_project_simulate.lock = self

        # Ensure devel lookups are forgotten.
        memoize_session_reset()

    def __exit__(self, exc_type, exc_val, exc_tb):
        devel_project_simulate.lock = None

        # Ensure devel lookups are forgotten.
        memoize_session_reset()

@memoize(session=True)
def origin_devel_project(apiurl, project, package):
    if devel_project_simulate.lock:
        devel_project, devel_package = devel_project_simulate.lock.get(apiurl, project, package)
        if devel_project:
            return devel_project, devel_package

    for devel_project, devel_package in origin_devel_project_requests(apiurl, project, package):
        return devel_project, devel_package

    return devel_project_get(apiurl, project, package)

@memoize(session=True)
def origin_devel_projects(apiurl, project):
    projects = set(devel_projects(apiurl, project))

    for devel_project, _ in origin_devel_project_requests(apiurl, project):
        projects.add(devel_project)

    devel_whitelist = Config.get(apiurl, project).get('devel-whitelist', '').split()
    projects.update(devel_whitelist)

    return sorted(projects)

def origin_devel_project_requests(apiurl, project, package=None):
    config = config_load(apiurl, project)
    for request, action in request_action_list(apiurl, project, package, types=['change_devel', 'submit']):
        if action.type == 'submit' and entity_exists(apiurl, action.tgt_project, action.tgt_package):
            # Only consider initial submit.
            continue

        annotation = origin_annotation_load(request, action, config['review-user'])
        if not annotation:
            # No annotation means not reviewed.
            continue

        origin = annotation.get('origin')
        if origin and origin in config_origin_list(config, apiurl, project):
            # Not a devel origin so not relevant.
            continue

        if config['fallback-group'] in reviews_remaining(request):
            # Still pending approval.
            continue

        yield action.src_project, action.src_package
