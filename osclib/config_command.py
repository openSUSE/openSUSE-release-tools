class ConfigCommand(object):
    def __init__(self, api):
        self.api = api

    def perform(self, projects, key=None, value=None, append=False, clear=False):
        project_max_format = '{:<' + str(len(max(projects, key=len))) + '} {}'
        for project in projects:
            meta = self.api.get_prj_pseudometa(project)
            meta.setdefault('config', {})

            if clear:
                if key:
                    meta['config'].pop(key, None)
                else:
                    meta.pop('config', None)
                self.api.set_prj_pseudometa(project, meta)
            elif value:
                value_project = value
                if append:
                    value_project = ' '.join([meta['config'].get(key, ''), value_project.strip()])
                meta['config'][key] = value_project.strip()
                self.api.set_prj_pseudometa(project, meta)

            keys = [key] if key else meta.get('config', {}).keys()
            for key_print in keys:
                print('{} = {}'.format(
                    project_max_format.format(project, key_print) if len(projects) > 1 else key_print,
                    meta['config'].get(key_print)))
