import json
from osclib.core import package_kind
from osclib.core import project_remote_list
from osclib.origin import origin_updatable_map
from osclib.origin import origin_update
from osclib.PubSubConsumer import PubSubConsumer
import threading


class OriginSourceChangeListener(PubSubConsumer):
    def __init__(self, apiurl, logger, project=None, dry=False):
        self.apiurl = apiurl
        self.project = project
        self.dry = dry
        self.listeners = {}

        amqp_prefix = 'suse' if self.apiurl.endswith('suse.de') else 'opensuse'
        super().__init__(amqp_prefix, logger)

    def run(self, runtime=None):
        super().run(runtime=runtime)

        for listener in self.listeners.values():
            listener.run(runtime=runtime)

    def stop(self):
        super().stop()

        for listener in self.listeners.values():
            listener.stop()

    def start_consuming(self):
        super().start_consuming()

        self.check_remotes()

    def routing_keys(self):
        return [self._prefix + k for k in [
            '.obs.package.commit',
            '.obs.package.delete',
            '.obs.request.create',
        ]]

    def on_message(self, unused_channel, method, properties, body):
        super().on_message(unused_channel, method, properties, body)

        payload = json.loads(body)
        if method.routing_key == '{}.obs.package.commit'.format(self._prefix):
            self.on_message_package_update(payload)
        elif method.routing_key == '{}.obs.package.delete'.format(self._prefix):
            self.on_message_package_update(payload)
        elif method.routing_key == '{}.obs.request.create'.format(self._prefix):
            self.on_message_request_create(payload)
        else:
            raise Exception('Unrequested message: {}'.format(method.routing_key))

    def on_message_package_update(self, payload):
        origins = self.origin_updatable_map()
        self.update_consider(origins, payload['project'], payload['package'])

    def on_message_request_create(self, payload):
        origins = self.origin_updatable_map(pending=True)
        for action in payload['actions']:
            # The following code demonstrates the quality of the data structure.
            # The base structure is inconsistent enough and yet the event data
            # structure manages to be different from XML structure (for no
            # reason) and even more inconsistent at that.
            if action['type'] == 'delete':
                if not action.get('targetpackage'):
                    continue

                project = action['targetproject']
                package = action['targetpackage']
            elif action['type'] == 'maintenance_incident':
                if action['sourcepackage'] == 'patchinfo':
                    continue
                project = action['target_releaseproject']
                if not action.get('targetpackage'):
                    package = action['sourcepackage']
                else:
                    repository_suffix_length = len(project) + 1 # package.project
                    package = action['targetpackage'][:-repository_suffix_length]
            elif action['type'] == 'maintenance_release':
                if action['sourcepackage'] == 'patchinfo':
                    continue
                project = action['targetproject']
                repository_suffix_length = len(project) + 1 # package.project
                package = action['sourcepackage'][:-repository_suffix_length]
            elif action['type'] == 'submit':
                project = action['targetproject']
                package = action['targetpackage']
            else:
                # Unsupported action type.
                continue

            self.update_consider(origins, project, package)

    def check_remotes(self):
        origins = self.origin_updatable_map()
        remotes = project_remote_list(self.apiurl)
        for remote, apiurl in remotes.items():
            for origin in origins:
                if origin.startswith(remote + ':') and apiurl not in self.listeners:
                    self.logger.info('starting remote listener due to {} origin'.format(origin))
                    self.listeners[apiurl] = OriginSourceChangeListenerRemote(apiurl, self, remote)
                    threading.Thread(target=self.listeners[apiurl].run, name=apiurl).start()

    def origin_updatable_map(self, pending=None):
        # include_self=True to check for updates whenever the target package is
        # updated. This will catch needed follow-up change_devel and handle
        # updates blocked by frequency control.
        return origin_updatable_map(self.apiurl, pending=pending, include_self=not pending)

    def update_consider(self, origins, origin_project, package):
        if origin_project not in origins:
            self.logger.info('skipped irrelevant origin: {}'.format(origin_project))
            return

        for project in origins[origin_project]:
            if self.project and project != self.project:
                self.logger.info('skipping filtered target project: {}'.format(project))
                continue

            # Check if package is of kind source in either target or origin
            # project -- this allows for deletes and new submissions. Execute
            # the checks lazily since they are expensive.
            if (package_kind(self.apiurl, project, package) == 'source' or
                package_kind(self.apiurl, origin_project, package) == 'source'):
                self.logger.info('checking for updates to {}/{}...'.format(project, package))
                request_future = origin_update(self.apiurl, project, package)
                if request_future:
                    request_future.print_and_create(self.dry)
            else:
                self.logger.info('skipped updating non-existant package {}/{}'.format(project, package))

class OriginSourceChangeListenerRemote(OriginSourceChangeListener):
    def __init__(self, apiurl, parent, prefix):
        self.parent = parent
        self.prefix = prefix

        super().__init__(apiurl, self.parent.logger)
        self._run_until = self.parent._run_until

    def check_remotes(self):
        pass

    def origin_updatable_map(self, pending=None):
        return self.parent.origin_updatable_map(pending=pending)

    def update_consider(self, origins, origin_project, package):
        origin_project = '{}:{}'.format(self.prefix, origin_project)
        self.parent.update_consider(origins, origin_project, package)
