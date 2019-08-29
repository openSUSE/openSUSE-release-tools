var OBS_URL = obs_url();
var FETCH_CONFIG = {
    method: 'GET',
    mode: 'cors',
    credentials: 'include',
};
var FETCH_JSON_CONFIG = Object.assign({}, FETCH_CONFIG, {
    headers: {
        'Accept': 'application/json',
    },
});
var POST_CONFIG = Object.assign({}, FETCH_CONFIG, {
    method: 'POST',
});

function origin_project(origin) {
    return origin.replace(/[+~]$/, '')
}

function param_lookup_package(cell) {
    return {
        urlPrefix: OBS_URL + '/package/show/' + project_get() + '/',
        target: '_blank',
    };
}

function param_lookup_origin(cell) {
    var origin = cell.getValue();
    if (origin == 'None') {
        return { url: '#' };
    }

    var params = {
        labelField: 'origin',
        urlField: 'package',
        urlPrefix: OBS_URL + '/package/show/' + origin_project(origin) + '/',
        target: '_blank',
    };
    if (cell.getTable().package) {
        params['url'] = params['urlPrefix'] + cell.getTable().package;
        params['urlPrefix'] = null;
    }

    return params;
}

function param_lookup_request(cell) {
    if (cell.getValue() == null) {
        return { label: ' ', url: '#' }
    }
    return {
        urlPrefix: OBS_URL + '/request/show/',
        target: '_blank',
    };
}

function formatter_tristate(cell, formatterParams, onRendered) {
    onRendered(function() {
        $(cell.getElement()).sparkline(cell.getValue(), {
            type: 'tristate',
            width: '100%',
            barWidth: 14,
            disableTooltips: true,
        });
    });
}

function sorter_tristate(a, b, aRow, bRow, column, dir, sorterParams) {
    return sorter_tristate_distance(a) - sorter_tristate_distance(b);
}

function sorter_request(a, b, aRow, bRow, column, dir, sorterParams) {
    if (a == b) return 0;
    if (a == null) return (dir == 'asc' ?  1 : -1) * Number.MAX_SAFE_INTEGER;
    if (b == null) return (dir == 'asc' ? -1 :  1) * Number.MAX_SAFE_INTEGER;
    return a - b;
}

function sorter_tristate_distance(revisions) {
    var distance = 0;
    for (var i = revisions.length - 1; i >= 0; i--) {
        if (revisions[i] === -1) distance += 10;
        else if (revisions[i] === 0) distance += 1;
        else break
    }

    return distance
}

function table_selection_get(table) {
    if (table.getSelectedRows().length > 0) {
        return table.getSelectedRows()[0].getIndex();
    }
    return null;
}

function table_selection_set(table, value) {
    if (typeof table === 'undefined') return;

    if (table.getSelectedRows().length > 0) {
        if (table.getSelectedRows()[0].getIndex() != value) {
            table.getSelectedRows()[0].deselect();
            table.selectRow(value);
            setTimeout(function(){ table.scrollToRow(value, 'middle', false) }, 500);
        }
    } else {
        table.selectRow(value);
        setTimeout(function(){ table.scrollToRow(value, 'middle', false) }, 500);
    }
}

var project_table;
function project_table_init(selector) {
    project_table = new Tabulator(selector, {
        columns: [
            {
                title: 'Package',
                field: 'package',
                headerFilter: 'input',
                width: 200,
                formatter: 'link',
                formatterParams: param_lookup_package,
            },
            {
                title: 'Origin',
                field: 'origin',
                headerFilter: 'input',
                width: 200,
                formatter: 'link',
                formatterParams: param_lookup_origin,
            },
            {
                title: 'Revisions',
                field: 'revisions',
                width: 170,
                formatter: formatter_tristate,
                sorter: sorter_tristate,
            },
            {
                title: 'Request',
                field: 'request',
                headerFilter: 'input',
                width: 100,
                formatter: 'link',
                formatterParams: param_lookup_request,
                sorter: sorter_request,
            },
        ],
        dataLoaded: package_select_set,
        index: 'package',
        initialSort: [
            { column: 'package', dir: 'asc' },
        ],
        rowClick: package_select_hash,
        selectable: 1,
        tooltips: true,
    });
}

var potential_table;
function potential_table_init(selector) {
    potential_table = new Tabulator(selector, {
        columns: [
            {
                title: 'Origin',
                field: 'origin',
                headerFilter: 'input',
                width: 200,
                formatter: 'link',
                formatterParams: param_lookup_origin,
            },
            {
                title: 'Version',
                field: 'version',
                headerFilter: 'input',
                width: 100,
            },
            {
                title: '',
                formatter: function(cell, formatterParams, onRendered) {
                    return "<i class='fa fa-stream'></i>";
                },
                width: 20,
                headerSort: false,
                tooltip: 'diff',
                cellClick: potential_external,
            },
            {
                title: '',
                formatter: function(cell, formatterParams, onRendered) {
                    return "<i class='fa fa-share-square'></i>";
                },
                width: 20,
                headerSort: false,
                tooltip: 'submit',
                cellClick: potential_submit_prompt,
            },
        ],
        dataLoaded: potential_select_set,
        index: 'origin',
        initialSort: [
            { column: 'origin', dir: 'asc' },
        ],
        rowClick: potential_select_hash,
        selectable: 1,
        tooltips: true,
    });
}

var history_table;
function history_table_init(selector) {
    history_table = new Tabulator(selector, {
        columns: [
            {
                title: 'Origin',
                field: 'origin',
                headerFilter: 'input',
                width: 200,
                formatter: 'link',
                formatterParams: param_lookup_origin,
            },
            {
                title: 'Request',
                field: 'request',
                headerFilter: 'input',
                width: 100,
                formatter: 'link',
                formatterParams: param_lookup_request,
                sorter: sorter_request,
            },
            {
                title: 'State',
                field: 'state',
                headerFilter: 'input',
                width: 100,
            },
        ],
        dataLoaded: history_select_set,
        index: 'request',
        initialSort: [
            { column: 'request', dir: 'desc' },
        ],
        rowClick: history_select_hash,
        selectable: 1,
        tooltips: true,
    });
}

function project_prompt() {
    const request = new Request(operator_url() + '/origin/projects/all', FETCH_JSON_CONFIG);
    fetch(request)
        .then(response => response.json())
        .then(projects => {
            var options = [];
            for (var i = 0; i < projects.length; i++) {
                options[i] = {text: projects[i], value: projects[i]};
            }

            bootbox.prompt({
                title: 'Project',
                inputType: 'select',
                inputOptions: options,
                closeButton: false,
                callback: function(project) {
                    if (project) {
                        hash_set([project]);
                    }
                }
            });
        });
}

function project_get() {
    if ('project' in project_table) {
        return project_table.project;
    }
    return null;
}

function project_set(project) {
    if (project == project_get()) {
        return;
    }
    project_table.project = project;
    project_table.clearData();
    project_table.setData(operator_url() + '/origin/list/' + project, {}, FETCH_JSON_CONFIG);
    project_table.setHeaderFilterFocus('package');
}

function package_get() {
    if (typeof potential_table !== 'undefined' && 'package' in potential_table) {
        return potential_table.package;
    }
    return null;
}

function package_set(project, package) {
    if (package == package_get()) {
        return;
    }
    $('aside').toggle(package != null);

    potential_table.package = package;
    history_table.package = package;

    if (package == null) return;

    potential_table.clearData();
    potential_table.setData(
        operator_url() + '/origin/potentials/' + project + '/' + package, {}, FETCH_JSON_CONFIG);

    history_table.clearData();
    history_table.setData(
        operator_url() + '/origin/history/' + project + '/' + package, {}, FETCH_JSON_CONFIG);

    package_select_set();
}

function package_select_set() {
    var package = package_get();
    if (package) {
        table_selection_set(project_table, package);
    }
}

function package_select_hash() {
    if (table_selection_get(project_table)) {
        hash_set([project_get(), table_selection_get(project_table),
                 origin_project(project_table.getSelectedRows()[0].getData()['origin'])]);
    } else {
        hash_set([project_get()]);
    }
}

function potential_get() {
    return hash_get(2);
}

function potential_set(project, package, origin, request) {
    if (project == $('#details').data('project') &&
        package == $('#details').data('package') &&
        origin == $('#details').data('origin') &&
        request == $('#details').data('request')) return;

    var path;
    if (request != null) {
        // Unlike a diff between origin and target project a diff between
        // a request and an origin requires the request source project and
        // package which are provided along with the history data. As such the
        // history table must be loaded before the diff can be requested.
        if (!history_table.getSelectedRows().length) return;

        var request_data = history_table.getSelectedRows()[0].getData();
        path = [
            origin,
            package,
            request_data['source_project'],
            request_data['source_package'],
            'latest',
            request_data['source_revision'],
        ].join('/');
    } else {
        path = [project, package, origin].join('/');
    }

    $('#details').toggle(origin != null);
    $('#details').data('project', project);
    $('#details').data('package', package);
    $('#details').data('origin', origin);
    $('#details').data('request', request);

    // At minimum an origin is required, but be sure to toggle element and set
    // data attributes to handle the next call properly.
    if (origin == null) return;

    $('#details').html('<p>Loading...</p>');

    fetch(operator_url() + '/package/diff/' + path, FETCH_CONFIG)
        .then(response => response.text())
        .then(text => {
            if (text == '') {
                $('#details').html('<p>No difference</p>');
                return;
            } else if (text.startsWith('# diff failed')) {
                $('#details').html('<p>Failed to generate diff</p><pre></pre>');
                $('#details pre').text(text);
                return;
            }
            $('#details').html(Diff2Html.getPrettyHtml(text));
        });

    potential_select_set();
}

function potential_select_set() {
    var origin = potential_get();
    if (origin) {
        table_selection_set(potential_table, origin);
    }
}

function potential_select_hash() {
    hash_set([project_get(), package_get(), table_selection_get(potential_table),
             table_selection_get(history_table)]);
}

function potential_external(e, cell) {
    window.open(OBS_URL + '/package/rdiff/' + cell.getData()['origin'] + '/' + package_get() +
      '?oproject=' + project_get(), '_blank');
}

function potential_submit_prompt(e, cell) {
    bootbox.prompt({
        title: 'Submit ' + cell.getData()['origin'] + '/' + package_get() + ' to ' + project_get() + '?',
        size: 'large',
        inputType: 'textarea',
        closeButton: false,
        callback: function(message) {
            if (message === null) return;
            potential_submit(project_get(), package_get(), cell.getData()['origin'], message);
        }
    });
}

function potential_submit(project, package, origin, message) {
    fetch(operator_url() + '/request/submit/' + origin + '/' + package + '/' + project +
            '?message=' + encodeURIComponent(message), POST_CONFIG)
        .then(response => response.text())
        .then(log => {
            log = log.trim()
            console.log(log);
            var words = log.split(/\s+/);
            var request = words[words.length - 1];
            if (request == 'failed') {
                bootbox.alert({
                    title: 'Failed to submit ' + origin + '/' + package + ' to ' + project,
                    message: '<pre>' + log.substr(0, log.length - 7) + '</pre>',
                    size: 'large',
                    closeButton: false,
                });
                return;
            }
            project_table.updateData([{'package': package, 'request': request}]);
        });
}

function history_get() {
    return hash_get(3);
}

function history_select_set() {
    var request = history_get();
    if (request) {
        table_selection_set(history_table, request);

        // Trigger appropriate potential_set() call since waiting for history
        // data to be available for request diff.
        hash_changed();
    }
}

function history_select_hash() {
    potential_select_hash();
}

var title_suffix;
function hash_init() {
    title_suffix = document.title;
    window.onhashchange = hash_changed;
    hash_changed();
}

function hash_parts() {
    return window.location.hash.substr(1).replace(/\/+$/, '').split('/');
}

function hash_get(index) {
    var parts = hash_parts();
    if (parts.length > index) {
        return parts[index];
    }
    return null;
}

function hash_set(parts) {
    // Shorten the parts array to before the first null.
    for (var i = 0; i < parts.length; i++) {
        if (parts[i] == null) {
            parts.length = i;
            break
        }
    }

    window.location.hash = parts.join('/');
}

function hash_changed() {
    // Wait until all tables have been initialized before proceeding.
    if (typeof history_table === 'undefined') return;

    var parts = hash_parts();
    var project = null;
    var package = null;
    var origin = null;
    var request = null;

    // route: /*
    if (parts[0] == '') {
        project_prompt();
        return;
    }

    // route: /:project
    project = parts[0];
    project_set(project);

    // route: /:project/:package
    if (parts.length >= 2) {
        package = parts[1];
    }
    package_set(project, package);

    // route: /:project/:package/:origin
    if (parts.length >= 3) {
        origin = parts[2];
    }

    // route: /:project/:package/:origin/:request
    if (parts.length >= 4) {
        request = parts[3];
    }
    potential_set(project, package, origin, request);

    title_update(project, package, origin, request);
}

function title_update(project, package, origin, request) {
    var parts = hash_parts();
    var title = '';
    if (project) {
        title += project;
    }
    if (package) {
        title += '/' + package;
    }
    if (request) {
        title += ' request ' + request;
    }
    if (origin) {
        title += ' diff against ' + origin;
    }

    if (title) {
        document.title = title + ' - ' + title_suffix;
    } else {
        document.title = title_suffix;
    }
}
