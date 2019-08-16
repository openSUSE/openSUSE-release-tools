from osc import conf
from osclib.common import VERSION

def sentry_init(obs_apiurl=None, tags=None):
    try:
        import sentry_sdk
    except ImportError:
        return sentry_sdk_dummy()

    sentry_init.client = sentry_sdk.init(
        conf.config.get('sentry_sdk.dsn'),
        environment=conf.config.get('sentry_sdk.environment'),
        release=VERSION)

    with sentry_sdk.configure_scope() as scope:
        if obs_apiurl:
            scope.set_tag('obs_apiurl', obs_apiurl)
            scope.user = {'username': conf.get_apiurl_usr(obs_apiurl)}

        if tags:
            for key, value in tags.items():
                scope.set_tag(key, value)

    return sentry_sdk

def sentry_client():
    return sentry_init.client

class sentry_sdk_dummy:
    def configure_scope(*args, **kw):
        return nop_class()

    def __getattr__(self, _):
        return nop_func

class nop_class:
    def __enter__(self):
        return nop_class()

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def __getattr__(self, _):
        return nop_func

def nop_func(*args, **kw):
    pass
