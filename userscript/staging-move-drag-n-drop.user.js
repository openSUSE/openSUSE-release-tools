// ==UserScript==
// @name         OSRT Staging Move Drag-n-Drop
// @namespace    openSUSE/openSUSE-release-tools
// @version      0.1.0
// @description  Provide staging request moving interface on staging dashboard.
// @author       Jimmy Berry
// @match        */project/staging_projects/*
// @require      https://code.jquery.com/jquery-3.3.1.min.js
// @require      https://raw.githubusercontent.com/p34eu/selectables/master/selectables.js
// @grant        none
// ==/UserScript==

// Uses a combination of two sources:
// - https://www.sitepoint.com/accessible-drag-drop/ (modified slightly)
// - https://github.com/p34eu/selectables (used directly with abuse of modifier key option)

(function()
{
    // Exclude not usable browsers.
    if (!document.querySelectorAll || !('draggable' in document.createElement('span'))) {
        return;
    }

    // Add explanation of trigger shortcut to legend box.
    var explanation = document.createElement('div');
    explanation.id = 'osrt-explanation';
    explanation.innerText = 'enter move mode';
    explanation.setAttribute('title', 'ctrl + m');
    explanation.onclick = function() {
        initMoveInterface();
        this.onclick = null;
    };
    document.querySelector('#legends').appendChild(explanation);

    window.onkeyup = function(e) {
        if (e.keyCode == 77 && e.ctrlKey) {
            initMoveInterface();
        }
    }

    // Include CSS immediately for explanation.
    $('head').append(`<style>
#osrt-explanation
{
    padding: 10px;
    background-color: #d9b200;
    color: white;
    cursor: pointer;
}

#osrt-explanation.osrt-active
{
    cursor: default;
}

#osrt-summary
{
    position: sticky;
    z-index: 10000;
    bottom: 0;
    left: 0;
    width: 95%;
    height: 100px;
    padding: 10px;
    background-color: black;
    color: #18f018;
    white-space: pre;
    overflow: scroll;
}

/* drop target state */
[data-draggable="target"][aria-dropeffect="move"]
{
    border-color: #68b;
}

[data-draggable="target"][aria-dropeffect="move"]:focus,
[data-draggable="target"][aria-dropeffect="move"].dragover
{
    box-shadow:0 0 0 1px #fff, 0 0 0 3px #68b;
}

[data-draggable="item"]:focus
{
    box-shadow: 0 0 0 2px #68b, inset 0 0 0 1px #ddd;
}

[data-draggable="item"][aria-grabbed="true"],
.color-legend .selected
{
    background: #8ad;
    color: #fff;
}

.color-legend .moved,
.osrt-moved
{
    background: repeating-linear-gradient(
        45deg,
        #606dbc,
        #606dbc 10px,
        #465298 10px,
        #465298 20px
    );
}
</style>`);

})();

var initMoveInterface = function(){
    // Update explanation text and add new legend entries.
    function addLegend(type)
    {
        var listItem = document.createElement('li');
        var span = document.createElement('span');
        span.classList.add(type.toLowerCase());
        listItem.appendChild(span);
        listItem.appendChild(document.createTextNode(type));
        document.querySelector('ul.color-legend').appendChild(listItem);
    }

    addLegend('Moved');
    addLegend('Selected');

    var explanation = document.querySelector('#osrt-explanation');
    explanation.innerText = 'drag box around requests or ctrl/shift + click requests to select and drag a request to another staging.';
    explanation.setAttribute('title', 'move mode activated');
    explanation.classList.add('osrt-active');

    // @resource will not work since served without proper MIME type.
    $.get('https://raw.githubusercontent.com/p34eu/selectables/master/selectables.css', function(data, status) {
        $('head').append('<style>' + data + '</style>');
    });

    // Mark the drag targets and draggable items.
    // Preferable to use the tr element as target, but mouse events not handled properly.
    // Avoid making expand/collapse links selectable and avoid forcing them to be expanded.
    // The pointer-events changes only seem to work properly from script.
    $('table.staging-dashboard td').attr('data-draggable', 'target');
    $('table.staging-dashboard ul.packages-list li.request:not(:has(a.staging_expand, a.staging_collapse))').attr('data-draggable', 'item').css('pointer-events', 'all');

    // Disable mouse events on the request links as that makes it nearly impossible to drag them.
    $('table.staging-dashboard ul.packages-list li.request:not(:has(a.staging_expand, a.staging_collapse)) a').css('pointer-events', 'none');

    // Configure selectables to play nice with drag-n-drop code.
    new Selectables({
        elements: 'ul.packages-list li[data-draggable="item"]',
        zone: 'body',
        start: function (e) {
            e.osrtContinue = (e.target.getAttribute('data-draggable') != 'item' &&
                              e.target.tagName != 'A' &&
                              e.target.tagName != 'LABEL' &&
                              e.target.id != 'osrt-summary');
        },
        // Abuse key option by setting the value in start callback whic is run
        // first and the value determines if drag selection is started.
        key: 'osrtContinue',
        onSelect: function (e) {
            addSelection(e);
        },
        onDeselect: function (e) {
            removeSelection(e);
        }
    });

    function getStaging(item)
    {
        var parent;
        if (item.tagName == 'TD') {
            parent = item.parentElement;
        } else {
            parent = item.parentElement.parentElement.parentElement;
        }
        if (item.parentElement.classList.contains('staging_collapsible')) {
            // An additional layer since in hidden container.
            parent = parent.parentElement;
        }
        return parent.querySelector('div.letter a').innerText;
    }

    function updateSummary()
    {
        var summaryElement = document.querySelector('div#osrt-summary');
        if (!summaryElement) {
            summaryElement = document.createElement('div');
            summaryElement.id = 'osrt-summary';
            document.body.appendChild(summaryElement);
        }

        var elements = document.querySelectorAll('.osrt-moved');
        var summary = {};
        var staging;
        for (var i = 0; i < elements.length; i++) {
            staging = getStaging(elements[i]);
            if (!(staging in summary)) {
                summary[staging] = [];
            }
            summary[staging].push(elements[i].children[0].innerText.trim());
        }

        var summaryText = '';
        var pathParts = window.location.pathname.split('/');
        var project = pathParts[pathParts.length - 1];
        for (var key in summary) {
            staging = key;
            if (!isNaN(key)) {
                staging = 'adi:' + key;
            }
            summaryText += 'osc staging -p ' + project + ' select --move ' + staging + ' ' + summary[key].join(' ') + "\n";
        }

        summaryElement.innerText = summaryText;
    }

    //get the collection of draggable targets and add their draggable attribute
    for(var
        targets = document.querySelectorAll('[data-draggable="target"]'),
        len = targets.length,
        i = 0; i < len; i ++)
    {
        targets[i].setAttribute('aria-dropeffect', 'none');
    }

    //get the collection of draggable items and add their draggable attributes
    for(var
        items = document.querySelectorAll('[data-draggable="item"]'),
        len = items.length,
        i = 0; i < len; i ++)
    {
        items[i].setAttribute('draggable', 'true');
        items[i].setAttribute('aria-grabbed', 'false');
        items[i].setAttribute('tabindex', '0');

        // OSRT modification: keep track of original staging.
        items[i].setAttribute('data-staging-origin', getStaging(items[i]));
    }

    //dictionary for storing the selections data
    //comprising an array of the currently selected items
    //a reference to the selected items' owning container
    //and a refernce to the current drop target container
    var selections =
    {
        items      : [],
        owner      : null,
        droptarget : null
    };

    //function for selecting an item
    function addSelection(item)
    {
        //if the owner reference is still null, set it to this item's parent
        //so that further selection is only allowed within the same container
        if(!selections.owner)
        {
            selections.owner = item.parentNode;
        }

        //or if that's already happened then compare it with this item's parent
        //and if they're not the same container, return to prevent selection
        else if(selections.owner != item.parentNode)
        {
            return;
        }

        //set this item's grabbed state
        item.setAttribute('aria-grabbed', 'true');

        //add it to the items array
        selections.items.push(item);
    }

    //function for unselecting an item
    function removeSelection(item)
    {
        //reset this item's grabbed state
        item.setAttribute('aria-grabbed', 'false');

        //then find and remove this item from the existing items array
        for(var len = selections.items.length, i = 0; i < len; i ++)
        {
            if(selections.items[i] == item)
            {
                selections.items.splice(i, 1);
                break;
            }
        }
    }

    //function for resetting all selections
    function clearSelections()
    {
        //if we have any selected items
        if(selections.items.length)
        {
            //reset the owner reference
            selections.owner = null;

            //reset the grabbed state on every selected item
            for(var len = selections.items.length, i = 0; i < len; i ++)
            {
                selections.items[i].setAttribute('aria-grabbed', 'false');
            }

            //then reset the items array
            selections.items = [];
        }
    }

    //shorctut function for testing whether a selection modifier is pressed
    function hasModifier(e)
    {
        return (e.ctrlKey || e.metaKey || e.shiftKey);
    }

    //function for applying dropeffect to the target containers
    function addDropeffects()
    {
        //apply aria-dropeffect and tabindex to all targets apart from the owner
        for(var len = targets.length, i = 0; i < len; i ++)
        {
            if
            (
                targets[i] != selections.owner
                &&
                targets[i].getAttribute('aria-dropeffect') == 'none'
            )
            {
                targets[i].setAttribute('aria-dropeffect', 'move');
                targets[i].setAttribute('tabindex', '0');
            }
        }

        //remove aria-grabbed and tabindex from all items inside those containers
        for(var len = items.length, i = 0; i < len; i ++)
        {
            if
            (
                items[i].parentNode != selections.owner
                &&
                items[i].getAttribute('aria-grabbed')
            )
            {
                items[i].removeAttribute('aria-grabbed');
                items[i].removeAttribute('tabindex');
            }
        }
    }

    //function for removing dropeffect from the target containers
    function clearDropeffects()
    {
        //if we have any selected items
        if(selections.items.length)
        {
            //reset aria-dropeffect and remove tabindex from all targets
            for(var len = targets.length, i = 0; i < len; i ++)
            {
                if(targets[i].getAttribute('aria-dropeffect') != 'none')
                {
                    targets[i].setAttribute('aria-dropeffect', 'none');
                    targets[i].removeAttribute('tabindex');
                }
            }

            //restore aria-grabbed and tabindex to all selectable items
            //without changing the grabbed value of any existing selected items
            for(var len = items.length, i = 0; i < len; i ++)
            {
                if(!items[i].getAttribute('aria-grabbed'))
                {
                    items[i].setAttribute('aria-grabbed', 'false');
                    items[i].setAttribute('tabindex', '0');
                }
                else if(items[i].getAttribute('aria-grabbed') == 'true')
                {
                    items[i].setAttribute('tabindex', '0');
                }
            }
        }
    }

    //shortcut function for identifying an event element's target container
    function getContainer(element)
    {
        do
        {
            if(element.nodeType == 1 && element.getAttribute('aria-dropeffect'))
            {
                return element;
            }
        }
        while(element = element.parentNode);

        return null;
    }

    //mousedown event to implement single selection
    document.addEventListener('mousedown', function(e)
    {
        //if the element is a draggable item
        if(e.target.getAttribute('draggable'))
        {
            //clear dropeffect from the target containers
            clearDropeffects();

            //if the multiple selection modifier is not pressed
            //and the item's grabbed state is currently false
            if
            (
                !hasModifier(e)
                &&
                e.target.getAttribute('aria-grabbed') == 'false'
            )
            {
                //clear all existing selections
                clearSelections();

                //then add this new selection
                addSelection(e.target);
            }
        }

        //else [if the element is anything else]
        //and the selection modifier is not pressed
        else if(!hasModifier(e))
        {
            //clear dropeffect from the target containers
            clearDropeffects();

            //clear all existing selections
            clearSelections();
        }

        //else [if the element is anything else and the modifier is pressed]
        else
        {
            //clear dropeffect from the target containers
            clearDropeffects();
        }

    }, false);

    //mouseup event to implement multiple selection
    document.addEventListener('mouseup', function(e)
    {
        //if the element is a draggable item
        //and the multipler selection modifier is pressed
        if(e.target.getAttribute('draggable') && hasModifier(e))
        {
            //if the item's grabbed state is currently true
            if(e.target.getAttribute('aria-grabbed') == 'true')
            {
                //unselect this item
                removeSelection(e.target);

                //if that was the only selected item
                //then reset the owner container reference
                if(!selections.items.length)
                {
                    selections.owner = null;
                }
            }

            //else [if the item's grabbed state is false]
            else
            {
                //add this additional selection
                addSelection(e.target);
            }
        }

    }, false);

    //dragstart event to initiate mouse dragging
    document.addEventListener('dragstart', function(e)
    {
        //if the element's parent is not the owner, then block this event
        if(selections.owner != e.target.parentNode)
        {
            e.preventDefault();
            return;
        }

        //[else] if the multiple selection modifier is pressed
        //and the item's grabbed state is currently false
        if
        (
            hasModifier(e)
            &&
            e.target.getAttribute('aria-grabbed') == 'false'
        )
        {
            //add this additional selection
            addSelection(e.target);
        }

        //we don't need the transfer data, but we have to define something
        //otherwise the drop action won't work at all in firefox
        //most browsers support the proper mime-type syntax, eg. "text/plain"
        //but we have to use this incorrect syntax for the benefit of IE10+
        e.dataTransfer.setData('text', '');

        //apply dropeffect to the target containers
        addDropeffects();

    }, false);

    //related variable is needed to maintain a reference to the
    //dragleave's relatedTarget, since it doesn't have e.relatedTarget
    var related = null;

    //dragenter event to set that variable
    document.addEventListener('dragenter', function(e)
    {
        related = e.target;

    }, false);

    //dragleave event to maintain target highlighting using that variable
    document.addEventListener('dragleave', function(e)
    {
        //get a drop target reference from the relatedTarget
        var droptarget = getContainer(related);

        //if the target is the owner then it's not a valid drop target
        if(droptarget == selections.owner)
        {
            droptarget = null;
        }

        //if the drop target is different from the last stored reference
        //(or we have one of those references but not the other one)
        if(droptarget != selections.droptarget)
        {
            //if we have a saved reference, clear its existing dragover class
            if(selections.droptarget)
            {
                selections.droptarget.className =
                    selections.droptarget.className.replace(/ dragover/g, '');
            }

            //apply the dragover class to the new drop target reference
            if(droptarget)
            {
                droptarget.className += ' dragover';
            }

            //then save that reference for next time
            selections.droptarget = droptarget;
        }

    }, false);

    //dragover event to allow the drag by preventing its default
    document.addEventListener('dragover', function(e)
    {
        //if we have any selected items, allow them to be dragged
        if(selections.items.length)
        {
            e.preventDefault();
        }

    }, false);

    //dragend event to implement items being validly dropped into targets,
    //or invalidly dropped elsewhere, and to clean-up the interface either way
    document.addEventListener('dragend', function(e)
    {
        //if we have a valid drop target reference
        //(which implies that we have some selected items)
        if(selections.droptarget)
        {
            // OSRT modification: only move if location is changing.
            if (getStaging(selections.droptarget) == getStaging(selections.items[0])) {
                e.preventDefault();
                return;
            }

            // OSRT modification: place requests back in package list.
            var target = selections.droptarget.parentElement.querySelector('ul.packages-list');

            //append the selected items to the end of the target container
            for(var len = selections.items.length, i = 0; i < len; i ++)
            {
                // OSRT modification: place in package list and determine if moved from origin.
                // selections.droptarget.appendChild(selections.items[i]);
                target.appendChild(selections.items[i]);
                if (getStaging(selections.items[i]) != selections.items[i].getAttribute('data-staging-origin'))
                {
                    selections.items[i].classList.add('osrt-moved');
                }
                else {
                    selections.items[i].classList.remove('osrt-moved');
                }
            }

            // OSRT modification: after drag update overall summary of moves.
            updateSummary();

            //prevent default to allow the action
            e.preventDefault();
        }

        //if we have any selected items
        if(selections.items.length)
        {
            //clear dropeffect from the target containers
            clearDropeffects();

            //if we have a valid drop target reference
            if(selections.droptarget)
            {
                //reset the selections array
                clearSelections();

                //reset the target's dragover class
                selections.droptarget.className =
                    selections.droptarget.className.replace(/ dragover/g, '');

                //reset the target reference
                selections.droptarget = null;
            }
        }

    }, false);
};
