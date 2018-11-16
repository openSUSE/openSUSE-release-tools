# User Scripts

The scripts may be installed in one's browser using the Tampermonkey extension to provide additional features using OBS via the web. After installing the extension simply click on the link for the desired script below to install it. Any scripts that provide an interface for making changes depend on the user being logged in to the OBS instance with a user with the appropriate permissions to complete the task.

- [Staging Move Drag-n-Drop](https://github.com/openSUSE/openSUSE-release-tools/raw/master/userscript/staging-move-drag-n-drop.user.js)

  Provides a drag-n-drop interface for moving requests between stagings using the staging dashboard. The staging dashboard can be found by visiting `/project/staging_projects/$PROJECT` on the relevant OBS instance where `$PROJECT` is the target project for the stagings (ex. `openSUSE:Factory` or `SUSE:SLE-15-SP1:GA`).

  Once on the staging dashboard the option to `enter move mode` will be available in the legend on the right side. Either click the yellow box or press _ctrl + m_ as indicated when hovering over the box. After entering _move mode_ individual requests can be dragged between stagings or groups selected and moved together. Groups may be selected by either clicking in an open area and dragging a box around the desired requests to select them and/or by hold _ctrl_ and clicking on requests to add or remove them from the selections.

  Once all desired moves have been made the _Apply_ button in the bottom center of the window may be press to apply the changes to the staging.

  Note that the staging lock is still in effect and thus the moves will fail if someone else has acquired the staging lock. Also note that after a failure or decision to not go through with moves there is currently no way to leave/reset move mode, but reloading the page will clear any changes made in move mode.

## Troubleshooting

Additional information after a failed operation is available in the browser console which may be accessed by _right-clicking_ on the page and selecting _Inspect_ or _Inspect Element_ and clicking the _Console_ tab.
