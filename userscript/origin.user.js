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

    $.get({url: url, crossDomain: true, xhrFields: {withCredentials: true}, success: function(origin) {
        if (origin.endsWith('failed')) return;
        $('ul.clean_list, ul.list-unstyled').append('<li>Origin: <a href="/package/show/' + origin + '/' + package + '">' + origin + '</a>');
    }});
})();
