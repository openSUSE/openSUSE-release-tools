#!/usr/bin/python3

from osclib.core import devel_project_get
from osclib.core import package_source_hash
from osclib.core import package_kind
from osclib.core import package_role_expand
from osclib.origin import origin_annotation_dump
from osclib.origin import origin_devel_projects
from osclib.origin import origin_workaround_strip
from osclib.origin import config_load
from osclib.origin import config_origin_list
from osclib.origin import devel_project_simulate
from osclib.origin import devel_project_simulate_exception
from osclib.origin import origin_find
from osclib.origin import policy_evaluate
from osclib.origin import PolicyResult
import ReviewBot
import sys


class OriginManager(ReviewBot.ReviewBot):
    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        # ReviewBot options.
        # Younger than default splitter-request-age-threshold to allow for quick
        # strategy to still be useful which requires a completed review.
        self.request_age_min_default = 30 * 60
        self.request_default_return = True
        self.override_allow = False

    def check_action_change_devel(self, request, action):
        advance, result = self.config_validate(action.tgt_project)
        if not advance:
            return result

        source_hash = package_source_hash(self.apiurl, action.tgt_project, action.tgt_package)
        origin_info_old = origin_find(self.apiurl, action.tgt_project, action.tgt_package, source_hash, True)

        with devel_project_simulate(self.apiurl, action.tgt_project, action.tgt_package,
                                    action.src_project, action.src_package):
            origin_info_new = origin_find(self.apiurl, action.tgt_project, action.tgt_package, source_hash)
            result = policy_evaluate(self.apiurl, action.tgt_project, action.tgt_package,
                                     origin_info_new, origin_info_old,
                                     source_hash, source_hash)

        reviews = {}

        # Remove all additional_reviews as there are no source changes.
        for key, comment in result.reviews.items():
            if key in ('fallback', 'maintainer'):
                reviews[key] = comment

        if result.accept:
            config = config_load(self.apiurl, action.tgt_project)
            if request.creator == config['review-user']:
                # Remove all reviews since the request was generated via
                # origin_update() which indicates it was approved already. Acts
                # as workaround for to lack of set devel on a submit request.
                reviews = {}

        if len(reviews) != len(result.reviews):
            result = PolicyResult(result.wait, result.accept, reviews, result.comments)

        return self.policy_result_handle(action.tgt_project, action.tgt_package, origin_info_new, origin_info_old, result)

    def check_action_delete_package(self, request, action):
        advance, result = self.config_validate(action.tgt_project)
        if not advance:
            return result

        origin_info_old = origin_find(self.apiurl, action.tgt_project, action.tgt_package)

        reviews = {'fallback': 'Delete requests require fallback review.'}
        self.policy_result_reviews_add(action.tgt_project, action.tgt_package,
                                       reviews, origin_info_old, origin_info_old)

        return True

    def check_source_submission(self, src_project, src_package, src_rev, tgt_project, tgt_package):
        kind = package_kind(self.apiurl, tgt_project, tgt_package)
        if not (kind is None or kind == 'source'):
            self.review_messages['accepted'] = 'skipping {} package since not source'.format(kind)
            return True

        advance, result = self.config_validate(tgt_project)
        if not advance:
            return result

        if self.request_age_wait():
            # Allow for parallel submission to be created.
            return None

        source_hash_new = package_source_hash(self.apiurl, src_project, src_package, src_rev)
        origin_info_new = origin_find(self.apiurl, tgt_project, tgt_package, source_hash_new)

        source_hash_old = package_source_hash(self.apiurl, tgt_project, tgt_package)
        origin_info_old = origin_find(self.apiurl, tgt_project, tgt_package, source_hash_old, True)

        # Check if simulating the devel project is appropriate.
        devel_project, reason = self.devel_project_simulate_check(src_project, tgt_project)
        if devel_project and (reason.startswith('change_devel command') or origin_info_new is None):
            self.logger.debug(f'reevaluate considering {devel_project} as devel since {reason}')

            try:
                with devel_project_simulate(self.apiurl, tgt_project, tgt_package, src_project, src_package):
                    # Recurse with simulated devel project.
                    ret = self.check_source_submission(
                        src_project, src_package, src_rev, tgt_project, tgt_package)
                    self.review_messages['accepted']['comment'] = reason
                    return ret
            except devel_project_simulate_exception:
                # Invalid infinite recursion so fallback to normal behavior.
                pass

        result = policy_evaluate(self.apiurl, tgt_project, tgt_package,
                                 origin_info_new, origin_info_old,
                                 source_hash_new, source_hash_old)
        return self.policy_result_handle(tgt_project, tgt_package, origin_info_new, origin_info_old, result)

    def config_validate(self, target_project):
        config = config_load(self.apiurl, target_project)
        if not config:
            # No perfect solution for lack of a config. For normal projects a
            # decline seems best, but in the event of failure to return proper
            # config no good behavior. For maintenance the situation is further
            # complicated since multiple actions some of which are not intended
            # to be reviewed, but not always guaranteed to see multiple actions.
            self.review_messages['accepted'] = 'skipping since no OSRT:OriginConfig'
            return False, True
        if not config.get('fallback-group'):
            self.review_messages['declined'] = 'OSRT:OriginConfig.fallback-group missing'
            return False, False
        if not self.dryrun and config['review-user'] != self.review_user:
            self.logger.warning(
                'OSRT:OriginConfig.review-user ({}) does not match ReviewBot.review_user ({})'.format(
                    config['review-user'], self.review_user))

        return True, True

    def devel_project_simulate_check(self, source_project, target_project):
        config = config_load(self.apiurl, target_project)
        origin_list = config_origin_list(config, self.apiurl, target_project, skip_workarounds=True)

        if '<devel>' not in origin_list:
            return False, None

        if len(origin_list) == 1:
            return source_project, 'only devel origin allowed'

        if source_project in origin_devel_projects(self.apiurl, target_project):
            return source_project, 'familiar devel origin'

        override, who = self.devel_project_simulate_check_command(source_project, target_project)
        if override:
            return override, 'change_devel command by {}'.format(who)

        return False, None

    def devel_project_simulate_check_command(self, source_project, target_project):
        who_allowed = self.request_override_check_users(target_project)
        if self.request.creator not in who_allowed:
            who_allowed.append(self.request.creator)

        for args, who in self.request_commands('change_devel', who_allowed):
            override = args[1] if len(args) >= 2 else source_project
            return override, who

        return False, None

    def policy_result_handle(self, project, package, origin_info_new, origin_info_old, result):
        if result.wait and not result.accept:
            result.comments.append(f'Decision may be overridden via `@{self.review_user} override`.')

        self.policy_result_reviews_add(project, package, result.reviews, origin_info_new, origin_info_old)
        self.policy_result_comment_add(project, package, result.comments)

        if result.wait:
            # Allow overriding a policy wait by accepting as workaround with the
            # hope that pending request will be accepted.
            override = self.request_override_check(True)
            if override:
                self.review_messages['accepted'] = origin_annotation_dump(
                    origin_info_new, origin_info_old, self.review_messages['accepted'], raw=True)
                return override
        else:
            if result.accept:
                self.review_messages['accepted'] = origin_annotation_dump(
                    origin_info_new, origin_info_old, raw=True)
            return result.accept

        return None

    def policy_result_reviews_add(self, project, package, reviews, origin_info_new, origin_info_old):
        for key, comment in reviews.items():
            if key == 'maintainer':
                self.origin_maintainer_review_ensure(origin_info_new, package, message=comment)
            elif key == 'fallback':
                fallback_group = config_load(self.apiurl, project).get('fallback-group')
                comment += '\n\n' + origin_annotation_dump(origin_info_new, origin_info_old)
                self.add_review(self.request, by_group=fallback_group, msg=comment)
            else:
                self.add_review(self.request, by_group=key, msg=comment)

    def origin_maintainer_review_ensure(self, origin_info, package, message, request=None):
        if not request:
            request = self.request

        origin = origin_workaround_strip(origin_info.project)

        # Check if the origin package is actually developed elsewhere and add
        # the maintainer review for the development location.
        devel_project, _ = devel_project_get(self.apiurl, origin, package)
        if devel_project:
            origin = devel_project

        users = package_role_expand(self.apiurl, origin, package, 'maintainer')
        if request.creator not in users:
            self.add_review(request, by_project=origin, by_package=package, msg=message)

    def policy_result_comment_add(self, project, package, comments):
        message = '\n\n'.join(comments)
        if len(self.request.actions) > 1:
            message = '## {}/{}\n\n{}'.format(project, package, message)
            suffix = '::'.join([project, package])
        else:
            suffix = None

        only_replace = False
        if not len(comments):
            message = 'Previous comment no longer relevant.'
            only_replace = True

        self.comment_write(state='seen', message=message, identical=True,
                           only_replace=only_replace, bot_name_suffix=suffix)


if __name__ == '__main__':
    app = ReviewBot.CommandLineInterface()
    app.clazz = OriginManager
    sys.exit(app.main())
