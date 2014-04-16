#! /bin/sh

# call with Snapshot20140402
osc release openSUSE:Factory _product:openSUSE-ftp-ftp-i586_x86_64

# make sure the FTP repo is first and the mini isos last
for i in kiwi-image-livecd-kde.i586 kiwi-image-livecd-kde.x86_64 kiwi-image-livecd-gnome.i586 kiwi-image-livecd-gnome.x86_64 kiwi-image-livecd-x11; do
  osc release openSUSE:Factory:Live $i --set-release $1
done
for i in _product:openSUSE-dvd5-dvd-i586 _product:openSUSE-dvd5-dvd-x86_64 _product:openSUSE-cd-mini-i586 _product:openSUSE-cd-mini-x86_64; do
    osc release openSUSE:Factory $i --set-release $1
done

