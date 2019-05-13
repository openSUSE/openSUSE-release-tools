// ==UserScript==
// @name         OSRT Origin
// @namespace    openSUSE/openSUSE-release-tools
// @version      0.1.0
// @description  Supplement OBS interface with origin information.
// @author       Jimmy Berry
// @match        */package/show/*
// @require      https://code.jquery.com/jquery-3.3.1.min.js
// @grant        none
// ==/UserScript==

(function()
{
    var pathParts = window.location.pathname.split('/');
    var project = pathParts[pathParts.length - 2];
    var package = pathParts[pathParts.length - 1];

    var domain_parent = window.location.hostname.split('.').splice(1).join('.');
    var subdomain = domain_parent.endsWith('suse.de') ? 'tortuga' : 'operator';
    var url = 'https://' + subdomain + '.' + domain_parent + '/origin/package/' + project + '/' + package;

    $('ul.clean_list, ul.list-unstyled').append('<li id="osrt-origin"><i class="fas fa-spinner fa-spin text-info"></i> loading origin...</li>');
    $.get({url: url, crossDomain: true, xhrFields: {withCredentials: true}, success: function(origin) {
        if (origin.endsWith('failed')) {
            if (origin.startsWith('OSRT:OriginConfig attribute missing')) {
                $('#osrt-origin').html('');
            } else {
                $('#osrt-origin').html('<i class="fas fa-bug text-warning"></i> failed to get origin info');
            }
        } else {
            project = origin.trim();
            if (project.endsWith('~')) {
                project = project.slice(0, -1);
            }
            $('#osrt-origin').html('<i class="fas fa-external-link-alt text-info"></i> <a href="/package/show/' + project + '/' + package + '">' + origin + '</a>');
        }
    }});
})();
