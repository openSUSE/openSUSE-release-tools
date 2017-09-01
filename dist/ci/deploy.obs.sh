#!/bin/bash

cat << eom > ~/.oscrc
[general]
apiurl = https://api.opensuse.org
[https://api.opensuse.org]
user = $OBS_USER
pass = $OBS_PASS
email = $OBS_EMAIL
eom

osc checkout "$OBS_PACKAGE"
cd "$OBS_PACKAGE"

rm *.obscpio
osc service disabledrun

# ensure _servicedata ends with a newline
tail -n1 _servicedata | read -r _ || echo >> _servicedata

osc addremove
osc commit -m "$(grep -oP 'version: \K.*' *.obsinfo)"

# Create submit request if none currently exists.
OBS_TARGET_PROJECT="$(osc info | grep -oP "Link info:.*?project \K[^\s,]+")"
OBS_TARGET_PACKAGE="$(osc info | grep -oP "Link info:.*?, package \K[^\s,]+")"
echo "checking for existing requests to $OBS_TARGET_PROJECT/$OBS_TARGET_PACKAGE..."
if osc request list "$OBS_TARGET_PROJECT" "$OBS_TARGET_PACKAGE" | grep 'No results for package' ; then
  osc service wait
  osc sr --diff | cat
  osc sr --yes -m "automatic update"
fi
