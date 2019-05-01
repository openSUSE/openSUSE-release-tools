function operator_url() {
    var domain_parent = window.location.hostname.split('.').splice(1).join('.');
    var subdomain = domain_parent.endsWith('suse.de') ? 'tortuga' : 'operator';
    return 'https://' + subdomain + '.' + domain_parent;
}

function obs_url() {
    var domain_parent = window.location.hostname.split('.').splice(1).join('.');
    return 'https://build.' + domain_parent;
}
