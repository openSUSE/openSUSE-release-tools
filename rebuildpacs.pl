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

my %leafed;

sub read_plain_index($) {
  my $file = shift;

  my %ret;

  open(FILE, $file) || return \%ret;
  while ( <FILE> ) {
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

  open(FILE, ">$file") || die "can't write to $file";
  for my $key (sort keys %{$hash}) {
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
          bundle-lang-kde installation-images-openSUSE)
      ],
    "kdebase4-openSUSE" => [qw(bundle-lang-kde)],
  );

sub check_leaf_package($$) {
    my $package = shift;
    my $rebuildhash = shift;

    my @lines = ();
    open( OSC, "osc api /build/$project/$repo/$arch/$package/_buildinfo?internal=1|" );
    while (<OSC>) {
        chomp;
        if (m/<subpack>(.*)</) {
            $leafed{$1} = $package;
        }
        if (m/bdep name="([^"]*)"/) {
            my $parent = $leafed{$1};
            if ( $parent && $parent ne "rpmlint-mini" ) {
	      # I dislike grep
	      unless (grep { $_ eq $package } @{$parents{$parent}}) {
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
    my $newmd5 = $ctx->hexdigest;
    if ($rebuilds->{"$project/$repo/$arch/$package"} ne $newmd5) {

      $rebuildhash->{$package} = 1;
      for my $child (@{$parents{$package}}) {
	$rebuildhash->{$child} = 1;
      }
      $rebuilds->{"$project/$repo/$arch/$package"} = $newmd5;
      write_plain_index("buildinfos", $rebuilds);
    }
}

my %torebuild;
check_leaf_package("rpmlint", \%torebuild);
check_leaf_package("rpmlint-mini", \%torebuild);

check_leaf_package("branding-openSUSE", \%torebuild);
check_leaf_package("glib2-branding-openSUSE", \%torebuild);
check_leaf_package("PackageKit-branding-openSUSE", \%torebuild);
check_leaf_package("kiwi-config-openSUSE", \%torebuild);
check_leaf_package("xfce4-branding-openSUSE", \%torebuild);
check_leaf_package("kdebase4-openSUSE", \%torebuild);
check_leaf_package("kde-branding-openSUSE", \%torebuild);

check_leaf_package("bundle-lang-common", \%torebuild);
check_leaf_package("bundle-lang-kde", \%torebuild);
check_leaf_package("bundle-lang-gnome", \%torebuild);
check_leaf_package("installation-images-openSUSE", \%torebuild);
if (%torebuild) {
  my $api = "/build/$project?cmd=rebuild&repository=$repo&arch=$arch";
  for my $package (sort keys %torebuild) {
    $api .= "&package=" . uri_escape( $package );
  }
  system("osc api -X POST '$api'");
}

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

