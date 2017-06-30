#! /usr/bin/perl -w


use File::Basename;
use File::Temp qw/ tempdir  /;
use XML::Simple;
use Data::Dumper;
use Cwd;

use strict;

BEGIN {
  my ($wd) = $0 =~ m-(.*)/- ;
  $wd ||= '.';
  unshift @INC, $wd;
}

require CreatePackageDescr;

my $ret = 0;
my $arch = shift @ARGV;
my $dir = shift @ARGV;
my %toignore;
my $repodir;
while (@ARGV) {
    my $switch = shift @ARGV;
    if ( $switch eq "-f" ) {
        my $toignore = shift @ARGV;
        open( TOIGNORE, $toignore ) || die "can't open $toignore";
        while (<TOIGNORE>) {
            chomp;
            $toignore{$_} = 1;
        }
        close(TOIGNORE);
    }
    elsif ( $switch eq "-r" ) {
        $repodir = shift @ARGV;
    }
    else {
        print "read the source luke: $switch ? \n";
        exit(1);
    }
}

my %targets;

sub write_package($$) {
    my $ignore  = shift;
    my $package = shift;

    my $name = basename($package);
    if ($name =~ m/^[a-z0-9]{32}-/) { # repo cache
       $name =~ s,^[^-]*-(.*)\.rpm,$1,;
    } else {
       $name =~ s,^(.*)-[^-]*-[^-]*.rpm,$1,;
    }

    if ( $ignore == 1 && defined $toignore{$name} ) {
        return;
    }

    my $out = CreatePackageDescr::package_snippet($package);
    if ($out =~ m/=Pkg:    /) {
        print STDERR "ERROR: empty package snippet for: $name\n";
        exit(1);
    }
    print PACKAGES $out;
    return $name;
}

my @rpms  = glob("$repodir/*.rpm");
my $tmpdir = tempdir( "repochecker-XXXXXXX", TMPDIR => 1, CLEANUP => 1 );
my $pfile = $tmpdir . "/packages";
open( PACKAGES, ">", $pfile ) || die 'can not open';
print PACKAGES "=Ver: 2.0\n";

foreach my $package (@rpms) {
    write_package( 1, $package );
}

@rpms = glob("$dir/*.rpm");
foreach my $package (@rpms) {
    my $name = write_package( 0, $package );
    $targets{$name} = 1;
}

close(PACKAGES);

#print STDERR "calling installcheck\n";
#print STDERR Dumper(\%targets);
my $error_file = $tmpdir . "/error_file";
open(INSTALL, "/usr/bin/installcheck $arch $pfile 2> $error_file |")
  || die 'exec installcheck';
while (<INSTALL>) {
    chomp;
#    print STDERR "$_\n";
    next if (m/unknown line:.*Flx/);
    if ( $_ =~ m/can't install (.*)-([^-]*)-[^-\.]/ ) {

#        print STDERR "CI $1 " . $targets{$1} . "\n";
        if ( defined $targets{$1} ) {
            print "$_\n";
            while (<INSTALL>) {
                last if (m/^can't install /);
                print "$_";
            }
            $ret = 1;
            last;
        }
    }
}
close(INSTALL);

open(ERROR, '<', $error_file);
while (<ERROR>) {
    chomp;
    print STDERR "$_\n";
    $ret = 1;
}
close(ERROR);

#print STDERR "checking file conflicts\n";
my $cmd = sprintf( "perl %s/findfileconflicts $pfile", dirname($0) );
open(INSTALL, "$cmd 2> $error_file |") || die 'exec fileconflicts';
my $inc = 0;
while (<INSTALL>) {
    chomp;

#    print STDERR "$_\n";
    if ( $_ =~ m/found conflict of (.*)-[^-]*-[^-]* with (.*)-[^-]*-[^-]*:/ ) {
        $inc = 0;

#        print STDERR "F $1 $2 -$targets{$1}-$targets{$2}-\n";
        if ( defined $targets{$1} || defined $targets{$2} ) {
            $inc = 1;
            $ret = 1;
        }
    }
    if ($inc) {
        print "$_\n";
    }
}
close(INSTALL);

open(ERROR, '<', $error_file);
while (<ERROR>) {
    chomp;
    print STDERR "$_\n";
    $ret = 1;
}
close(ERROR);

#print STDERR "RET $ret\n";
exit($ret);
