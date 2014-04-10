#! /usr/bin/perl

use Data::Dumper;
use XML::Simple;
use URI::Escape;
use File::Basename;
use File::Temp qw/tempdir/;

my $script_dir;

BEGIN {
  ($script_dir) = $0 =~ m-(.*)/- ;
  $script_dir ||= '.';
  unshift @INC, $script_dir;
}

require CreatePackageDescr;

my $repodir;

sub find_source_container($) {
    my $pkg = shift;

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

my $followup = 0;
my $cproblem = '';
my %problems;

my $project = $ARGV[0] || "openSUSE:Factory";
my $repo    = $ARGV[1] || "standard";
my $arch    = $ARGV[2] || "x86_64";

$repodir = "/var/cache/repo-checker/repo-openSUSE:Factory-$repo-$arch";
mkdir($repodir);
my $pfile = tempdir() . "/packages";    # the filename is important ;(

system(
"$script_dir/bs_mirrorfull --nodebug https://build.opensuse.org/build/$project/$repo/$arch/ $repodir"
);

my @rpms = glob("$repodir/*.rpm");

open( PACKAGES, ">", $pfile ) || die "can not open $pfile";
print PACKAGES "=Ver: 2.0\n";

foreach my $package (@rpms) {
    my $out = CreatePackageDescr::package_snippet($package);
    print PACKAGES CreatePackageDescr::package_snippet($package);
}
close(PACKAGES);

# read the problems out of installcheck
open( INSTALLCHECK, "installcheck $arch $pfile|" );
while (<INSTALLCHECK>) {
    chomp;

    if (m/^can't install (.*)\-[^-]*\-[^-]*\.($arch|noarch):/) {
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

    s,(needed by [^ ]*)\-[^-]*\-[^-]*\.($arch|noarch)$,$1,;

    s,^\s*,,;
    $problems{$cproblem}->{$_} = 1;

}
close(INSTALLCHECK);

my %nproblems;
for my $package ( sort keys %problems ) {
    $problems{$package} = join( ', ', sort( keys %{ $problems{$package} } ) );
}

open( PROBLEMS, "problems" );
while (<PROBLEMS>) {
    chomp;
    if (m,^$project/$repo/$arch/([^:]*):\s*(.*)$,) {
        my $package  = $1;
        my $oproblem = $2;
        my $nproblem = $problems{$package};
        if ( $oproblem && $nproblem && $oproblem eq $nproblem )
        {    # one more rebuild won't help
            delete $problems{$package};
        }
    }
}
close(PROBLEMS);

exit(0) if ( !%problems );

# check for succeeded packages
my $api = "/build/$project/_result?repository=$repo&arch=$arch&code=succeeded";
for my $problem ( sort keys %problems ) {
    $api .= "&package=" . uri_escape($problem);
}

open( RESULT, "osc api '$api'|" );
@result = <RESULT>;
my $results = XMLin( join( '', @result ), ForceArray => ['status'] );
close(RESULT);

my @packages = @{ $results->{result}->{status} };
exit(0) if ( !@packages );

open( PROBLEMS, ">>problems" );
$api = "/build/$project?cmd=rebuild&repository=$repo&arch=$arch";
for my $package (@packages) {
    print "rebuild ", $package->{package}, ": ",
      $problems{ $package->{package} }, "\n";
    $api .= "&package=" . uri_escape( $package->{package} );
    print PROBLEMS "$project/$repo/$arch/"
      . $package->{package} . ": "
      . $problems{ $package->{package} }, "\n";
}

system("osc api -X POST '$api'");
close(PROBLEMS);

