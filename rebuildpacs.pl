#! /usr/bin/perl

use Data::Dumper;
use XML::Simple;
use URI::Escape;
use File::Basename;
use File::Temp qw/tempdir/;
use Digest::MD5 qw(md5_hex);

my $script_dir;

BEGIN {
    ($script_dir) = $0 =~ m-(.*)/-;
    $script_dir ||= '.';
    unshift @INC, $script_dir;
}

require CreatePackageDescr;

my @repodirs;

sub find_source_container($) {
    my $pkg = shift;

    for my $repodir (@repodirs) {
        my @rpms = glob("$repodir/*-$pkg.rpm");
        for my $rpm (@rpms) {

            # 1123 == Disturl
            my %qq = Build::Rpm::rpmq( $rpm, qw{NAME 1123} );
            next if ( $qq{NAME}[0] ne $pkg );
            my $distfile = basename( $qq{1123}[0] );
            $distfile =~ s,^[^-]*-,,;

            return $distfile;
        }
    }
}

my $followup = 0;
my $cproblem = '';
my %problems;

my $project = $ARGV[0] || "openSUSE:Factory";
my $repo    = $ARGV[1] || "standard";
my $arch    = $ARGV[2] || "x86_64";

my %leafed;

sub read_plain_index($) {
    my $file = shift;

    my %ret;

    open( FILE, $file ) || return \%ret;
    while (<FILE>) {
        if (m/^(.*):(.*)/) {
            $ret{$1} = $2;
        }
    }
    close(FILE);
    return \%ret;
}

sub write_plain_index($$) {
    my $file = shift;
    my $hash = shift;

    open( FILE, ">$file" ) || die "can't write to $file";
    for my $key ( sort keys %{$hash} ) {
        print FILE "$key:" . $hash->{$key} . "\n";
    }
    close(FILE);
}

# defines packages that need to be triggered too
my %parents = (
    "rpmlint"           => [qw(rpmlint-mini)],
    "branding-openSUSE" => [
        qw(glib2-branding-openSUSE
          kiwi-config-openSUSE
          xfce4-branding-openSUSE
          kdebase4-openSUSE kde-branding-openSUSE
          bundle-lang-kde bundle-lang-common
          openSUSE-images installation-images-openSUSE)
    ],
    "kdebase4-openSUSE" => [qw(bundle-lang-kde)],
    "kernel-source"     => [qw(perf)],
);

# for subsets (staging projects) we need to remember which are ignored
my %ignored;

sub check_leaf_package($$) {

    my $package     = shift;
    my $rebuildhash = shift;

    if ( system("osc api /source/$project/$package/_meta > /dev/null 2>&1 ") ) {
        $ignored{$package} = 1;
        return;
    }

    my @lines = ();
    open( OSC,
        "osc api /build/$project/$repo/$arch/$package/_buildinfo?internal=1|" );
    while (<OSC>) {
        chomp;
        if (m/<subpack>(.*)</) {
            $leafed{$1} = $package;
        }
        if (m/bdep name="([^"]*)"/) {
            my $parent = $leafed{$1};
            if ( $parent && $parent ne "rpmlint-mini" ) {

                # I dislike grep
                unless ( grep { $_ eq $package } @{ $parents{$parent} } ) {
                    print "ADD $package to PARENT $parent!!\n";
                }
                next;
            }
        }
        else {
            next;
        }
        next if (m/notmeta="1"/);
        push( @lines, $_ );
    }
    close(OSC);
    my $ctx = Digest::MD5->new;
    for my $line ( sort @lines ) {
        $ctx->add($line);
    }
    my $rebuilds = read_plain_index("buildinfos");
    my $newmd5   = $ctx->hexdigest;
    if ( $rebuilds->{"$project/$repo/$arch/$package"} ne $newmd5 ) {

        $rebuildhash->{$package} = 1;
        for my $child ( @{ $parents{$package} } ) {
            $rebuildhash->{$child} = 1;
        }
        $rebuilds->{"$project/$repo/$arch/$package"} = $newmd5;
        write_plain_index( "buildinfos", $rebuilds );
    }
}

my %torebuild;
check_leaf_package( "rpmlint",      \%torebuild );
check_leaf_package( "rpmlint-mini", \%torebuild );

check_leaf_package( "branding-openSUSE",            \%torebuild );
check_leaf_package( "glib2-branding-openSUSE",      \%torebuild );
check_leaf_package( "PackageKit-branding-openSUSE", \%torebuild );
check_leaf_package( "kiwi-config-openSUSE",         \%torebuild );
check_leaf_package( "xfce4-branding-openSUSE",      \%torebuild );
check_leaf_package( "kdebase4-openSUSE",            \%torebuild );
check_leaf_package( "kde-branding-openSUSE",        \%torebuild );

check_leaf_package( "bundle-lang-common",           \%torebuild );
check_leaf_package( "bundle-lang-kde",              \%torebuild );
check_leaf_package( "bundle-lang-gnome",            \%torebuild );
check_leaf_package( "installation-images-openSUSE", \%torebuild );
check_leaf_package( "openSUSE-images",              \%torebuild );
if (%torebuild) {
    my $api = "/build/$project?cmd=rebuild&repository=$repo&arch=$arch";
    for my $package ( sort keys %torebuild ) {
        next if ( defined $ignored{$package} );
        last if ( length($api) > 32767 );
        $api .= "&package=" . uri_escape($package);
    }
    system("osc api -X POST '$api'");
}

my $pfile =
  tempdir( CLEANUP => 1 ) . "/packages";    # the filename is important ;(

sub mirror_repo($$$) {

    my $project = shift;
    my $repo    = shift;
    my $arch    = shift;

    # Old and new in single directory, but never deployed together.
    my $repodir = ( $ENV{XDG_CACHE_HOME} || $ENV{HOME} . "/.cache" )
      . "/openSUSE-release-tools/repository-meta/repo-$project-$repo-$arch";
    mkdir($repodir);

    system(
"$script_dir/bs_mirrorfull --nodebug https://api.opensuse.org/public/build/$project/$repo/$arch/ $repodir"
    );
    return $repodir;
}

sub find_package_in_project($) {
    my $project = shift;

    open( OSC, "osc api /source/$project?expand=1 |" );
    my $xml = XMLin( join( '', <OSC> ), ForceArray => 1 );
    close(OSC);
    my @packs = keys %{ $xml->{entry} };
    return shift @packs;
}

# find a random package

sub get_paths($$$) {
    my $project = shift;
    my $repo    = shift;
    my $arch    = shift;

    my $package = find_package_in_project($project);

    open( OSC, "osc api /build/$project/$repo/$arch/$package/_buildinfo|" );
    my $xml = join( '', <OSC> );
    if ( $xml !~ m/^</ ) {
        die "failed to open /build/$project/$repo/$arch/$package/_buildinfo";
    }
    $xml = XMLin( $xml, ForceArray => 1 );
    close(OSC);

    return $xml->{path};
}

my $paths = get_paths( $project, $repo, $arch );
my @rpms;

for my $path (@$paths) {

    # openSUSE:Factory/ports is in the paths, but not a repo
    if (
        system(
"osc api /build/$path->{'project'}/$path->{'repository'}/$arch > /dev/null 2>&1 "
        )
      )
    {
        next;
    }

    my $repodir =
      mirror_repo( $path->{'project'}, $path->{'repository'}, $arch );
    push( @repodirs, $repodir );
    push( @rpms,     glob("$repodir/*.rpm") );
}

open( PACKAGES, ">", $pfile ) || die "can not open $pfile";
print PACKAGES "=Ver: 2.0\n";

my %knipser;

foreach my $package (@rpms) {
    die $package unless $package =~ m,/.{32}-([^/]+)\.rpm$,;
    next if $knipser{$1}++;
    my $out = CreatePackageDescr::package_snippet($package);
    print PACKAGES $out;
}
close(PACKAGES);

# read the problems out of installcheck
my $rpmarch = $arch;
$rpmarch = "armv7hl" if ( $arch eq "armv7l" );
$rpmarch = "armv6hl" if ( $arch eq "armv6l" );

open( INSTALLCHECK, "/usr/bin/installcheck $rpmarch $pfile|" );
while (<INSTALLCHECK>) {
    chomp;

    if (m/^can't install (.*)\-[^-]*\-[^-]*\.($rpmarch|noarch):/) {
        $cproblem = $1;
        $cproblem =~ s/kmp-([^-]*)/kmp-default/;
        $cproblem = find_source_container($cproblem);
        $followup = 0;
        next;
    }

    $followup = 1 if ( $_ =~ m/none of the providers can be installed/ );

    # not interesting for me
    next if (m/  \(we have /);
    next if ($followup);

    # very thin ice here
    s,\(\)\(64bit\),,;

    s,(needed by [^ ]*)\-[^-]*\-[^-]*\.($rpmarch|noarch)$,$1,;

    s,^\s*,,;

    # patterns are too spammy and rebuilding doesn't help
    next
      if (
        grep { $_ eq $cproblem }
        qw(
        fftw3:gnu-openmpi-hpc
        hdf5:gnu-hpc
        hdf5:gnu-mpich-hpc
        hdf5:gnu-mvapich2-hpc
        hdf5:gnu-openmpi-hpc
        hdf5:gnu-openmpi2-hpc
        hdf5:gnu-openmpi3-hpc
        hdf5:mvapich2
        hdf5:openmpi
        hdf5:serial
        installation-images:Kubic
        metis:gnu-hpc
        netcdf:gnu-hpc
        netcdf:gnu-mvapich2-hpc
        netcdf:gnu-openmpi-hpc
        netcdf:openmpi
        netcdf:serial
        patterns-base
        patterns-haskell
        patterns-mate
        patterns-media
        patterns-openSUSE
        patterns-yast
        petsc:serial
        python-numpy:gnu-hpc
        scalapack:gnu-mvapich2-hpc
        scalapack:gnu-openmpi-hpc
        warewulf:modules
        python-scipy:gnu-hpc
        )
      );
    $problems{$cproblem}->{$_} = 1;

}
close(INSTALLCHECK);
unlink($pfile);
rmdir( dirname($pfile) );

for my $package ( sort keys %problems ) {
    $problems{$package} = join( ', ', sort( keys %{ $problems{$package} } ) );
}

my @other_problems;
my %oproblems;

open( PROBLEMS, "problems" );
while (<PROBLEMS>) {
    chomp;
    if (m,^$project/$repo/$arch/([^:]*):\s*(.*)$,) {
        my $package  = $1;
        my $oproblem = $2;

        # remember old problems for current project/repo
        $oproblems{$package} = $oproblem;
    }
    else {
        # keep all lines for other projects/repos as they are
        push @other_problems, $_;
    }
}
close(PROBLEMS);

exit(0) if ( !%problems );

# check for succeeded packages - we can't filter as we don't know if the problems are all in the top project ;(
my $api = "/build/$project/_result?repository=$repo&arch=$arch&code=succeeded";

open( RESULT, "osc api '$api'|" );
@result = <RESULT>;
my $results = XMLin( join( '', @result ), ForceArray => ['status'] );
close(RESULT);

my @packages  = @{ $results->{result}->{status} };
my $rebuildit = 0;

$api = "/build/$project?cmd=rebuild&repository=$repo&arch=$arch";
for my $package (@packages) {
    $package = $package->{package};
    last if ( length($api) > 32767 );

    if ( !$problems{$package} ) {

        # it can go
        delete $oproblems{$package};
        next;
    }

    my $oproblem = $oproblems{$package} || '';
    if ( $problems{$package} eq $oproblem ) {

        # rebuild won't help
        next;
    }
    $rebuildit = 1;
    print "rebuild ", $package, ": ", $problems{$package}, "\n";
    $api .= "&package=" . uri_escape($package);
    $oproblems{$package} = $problems{$package};
}

open( PROBLEMS, ">problems" );

# write all lines for other projects/repos as they are
foreach (@other_problems) {
    print PROBLEMS $_, "\n";
}
for my $package ( keys %oproblems ) {
    print PROBLEMS "$project/$repo/$arch/" . $package . ": "
      . $oproblems{$package}, "\n";
}
close(PROBLEMS);

if ($rebuildit) {
    print "API '$api'\n";
    system("osc api -X POST '$api'");
}
