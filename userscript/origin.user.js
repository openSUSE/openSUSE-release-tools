// ==UserScript==
// @name         OSRT Origin
// @namespace    openSUSE/openSUSE-release-tools
// @version      0.2.0
// @description  Supplement OBS interface with origin information.
// @author       Jimmy Berry
// @match        */package/show/*
// @match        */request/show/*
// @require      https://code.jquery.com/jquery-3.3.1.min.js
// @grant        none
// ==/UserScript==

(function()
{
    var pathParts = window.location.pathname.split('/');

    if (pathParts[1] == 'package') {
        var project = pathParts[pathParts.length - 2];
        var package = pathParts[pathParts.length - 1];
        origin_load(document.querySelector('ul.clean_list, ul.list-unstyled'), project, package);
    } else if (pathParts[1] == 'request') {
        request_actions_handle();
    }
})();

function request_actions_handle() {
    // Select all action tabs and store to avoid modification exceptions.
    var action_elements = document.evaluate(
        '//div[@class="card mb-3"][2]/div/div[@class="tab-content"]/div', document);
    var actions = [];
    var action;
    while (action = action_elements.iterateNext()) {
        actions.push(action);
    }

    for (var i = 0; i < actions.length; i++) {
        action = actions[i];

        // Select the side column containing build results.
        var column = document.evaluate(
            'div[@class="row"][2]//div[@class="card" and div[@data-buildresult-url]]',
            action, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;

        // Select the text represtation of action. All other sources are
        // inconsistent and do not always have the right values depending on
        // request type or state.
        var summary = document.evaluate(
            'div[1]/div[1]',
            action, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
        var parts = $(summary).text().trim().split(' ');

        // Maintenance incidents are so special.
        var release_project = document.evaluate(
            'i[1]',
            action, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
        var project, package;
        if (release_project) {
            parts = parts.splice(4, 3);
            project = $(release_project).text().trim().split(' ').splice(-1)[0];
        } else {
            parts = parts.splice(-3);
            project = parts[0];
        }
        package = parts[2];

        var card = document.createElement('div');
        card.classList.add('card');

        var list = document.createElement('ul');
        list.classList.add('list-unstyled');
        card.appendChild(list);

        column.insertBefore(card, column.childNodes[0]);

        origin_load(list, project, package);
    }
}

function origin_load(element, project, package) {
    // Add placeholder to indicated loading.
    var item = document.createElement('li');
    item.innerHTML = '<i class="fas fa-spinner fa-spin text-info"></i> Origin: loading...';
    element.appendChild(item);

    var url = operator_url() + '/origin/package/' + project + '/' + package;
    $.get({url: url, crossDomain: true, xhrFields: {withCredentials: true}, success: function(origin) {
        if (origin.endsWith('failed')) {
            if (origin.startsWith('OSRT:OriginConfig attribute missing')) {
                item.innerHTML = '';
            } else {
                item.innerHTML = '<i class="fas fa-bug text-warning"></i> Origin: failed to load';
            }
        } else {
            var origin_project = origin.trim();
            if (origin_project.endsWith('~')) {
                origin_project = origin_project.slice(0, -1);
            }
            item.innerHTML = '<i class="fas fa-external-link-alt text-info"></i> Origin: ';
            if (origin_project != 'None') {
                item.innerHTML += '<a href="/package/show/' + origin_project + '/' +
                    package + '">' + origin + '</a>'
            } else {
                item.innerHTML += origin;
            }

            url = web_interface_url() + '/web/origin-manager/#' + project + '/' + package;
            if (origin_project != 'None') {
                url += '/' + origin_project;
            }
            item = document.createElement('li');
            item.innerHTML = '<i class="fas fa-external-link-alt text-info"></i> ' +
                '<a target="_blank" href="' + url + '">Origin Manager Interface</a>';
            element.appendChild(item);
        }
    }});
}

function operator_url() {
    var domain_parent = window.location.hostname.split('.').splice(-2).join('.');
    var subdomain = domain_parent.endsWith('suse.de') ? 'tortuga' : 'operator';
    return 'https://' + subdomain + '.' + domain_parent;
}

function web_interface_url() {
    var domain_parent = window.location.hostname.split('.').splice(-2).join('.');
    var subdomain, path;
    if (domain_parent.endsWith('suse.de')) {
        subdomain = 'jberry.io';
        path = '/osrt-web';
    } else {
        subdomain = 'osrt';
        path = '';
    }
    return 'http://' + subdomain + '.' + domain_parent + path;
}
