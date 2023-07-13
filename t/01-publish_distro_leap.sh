set -e

work=t/tmp
rm -rf $work
mkdir $work

(
cd $work
mkdir ftp-stage
mkdir -p ftp/pub/opensuse/distribution/leap/15.5/iso


mkdir -p ftp/pub/opensuse/distribution/leap/15.5/repo/oss
mkdir -p ftp/pub/opensuse/distribution/leap/15.5/repo/non-oss



cp -r ftp/pub ftp-stage/

for arch in x86_64 aarch64 ppc64le s390x; do
    for flavor in DVD NET; do
        touch ftp-stage/pub/opensuse/distribution/leap/15.5/iso/openSUSE-Leap-15.5-$flavor-$arch-Build111.11-Media.iso
        touch ftp-stage/pub/opensuse/distribution/leap/15.5/iso/openSUSE-Leap-15.5-$flavor-$arch-Build111.11-Media.iso.sha256
    done
done
)


PUBLISH_DISTRO_BASE=$work/ bash ./publish_distro --dry --force publish_distro_conf/publish_leap155.config

