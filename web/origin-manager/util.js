function operator_url() {
    var domain_parent = window.location.hostname.split('.').splice(-2).join('.');
    var subdomain = domain_parent.endsWith('suse.de') ? 'tortuga' : 'operator';
    return 'https://' + subdomain + '.' + domain_parent;
}

function obs_url() {
    var domain_parent = window.location.hostname.split('.').splice(-2).join('.');
    return 'https://build.' + domain_parent;
}
