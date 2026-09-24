set -e

work=t/tmp
rm -rf $work
mkdir $work

(
cd $work
mkdir ftp-stage
mkdir -p ftp/pub/opensuse/tumbleweed/iso

mkdir -p ftp/pub/opensuse/tumbleweed/repo/oss
mkdir -p ftp/pub/opensuse/tumbleweed/repo/non-oss

cp -r ftp/pub ftp-stage/

for arch in x86_64; do
    for flavor in DVD NET GNOME-Live KDE-Live Rescue-CD; do
        touch ftp-stage/pub/opensuse/tumbleweed/iso/openSUSE-Tumbleweed-$flavor-$arch-Snapshot20230101-Media.iso
        touch ftp-stage/pub/opensuse/tumbleweed/iso/openSUSE-Tumbleweed-$flavor-$arch-Snapshot20230101-Media.iso.sha256
    done
done
)

PUBLISH_DISTRO_BASE=$work/ PUBLISH_DISTRO_DATE='20230101' . ./publish_distro --dry --force publish_distro_conf/publish_tumbleweed.config

