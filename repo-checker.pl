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

for my $pdir ( glob("$dir/*") ) {
    if ( !-f "$pdir/rpmlint.log" ) {
        print
	  "Couldn't find a rpmlint.log in the build results in $pdir. This is mandatory\n";
        my $name = basename($pdir);
        if (   $name eq "rpm"
            || $name eq "rpm-python"
            || $name eq "popt"
            || $name eq "rpmlint"
            || $name eq "rpmlint-mini"
            || $name eq "rpmlint-Factory" )
        {
            print "ignoring - whitelist\n";
        }
        else {
            $ret = 1;
        }
    }
    else {
        open( GREP, "grep 'W:.*invalid-license ' $pdir/rpmlint.log |" );
        while (<GREP>) {
            print "Found rpmlint warning: ";
            print $_;
            $ret = 1;
        }
    }
}

my %targets;

sub write_package($$) {
    my $ignore  = shift;
    my $package = shift;

    my $out = CreatePackageDescr::package_snippet($package);

    my $name = basename($package);
    if ($name =~ m/^[a-z0-9]{32}-/) { # repo cache
       $name =~ s,^[^-]*-(.*)\.rpm,$1,;
    } else {
       $name =~ s,^(.*)-[^-]*-[^-]*.rpm,$1,;
    }

    if ( $ignore == 1 && defined $toignore{$name} ) {
        return;
    }

    print PACKAGES $out;
    return $name;
}

my @rpms  = glob("$repodir/*.rpm");
my $pfile = $ENV{'HOME'} . "/packages";
open( PACKAGES, ">", $pfile ) || die 'can not open';
print PACKAGES "=Ver: 2.0\n";

foreach my $package (@rpms) {
    write_package( 1, $package );
}

@rpms = glob("$dir/*/*.rpm");
foreach my $package (@rpms) {
    my $name = write_package( 0, $package );
    $targets{$name} = 1;
}

close(PACKAGES);

#print STDERR "calling installcheck\n";
#print STDERR Dumper(\%targets);
open( INSTALL, "/usr/bin/installcheck x86_64 $pfile 2>&1|" )
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

#print STDERR "checking file conflicts\n";
my $cmd = sprintf( "perl %s/findfileconflicts $pfile", dirname($0) );
open( INSTALL, "$cmd |" ) || die 'exec fileconflicts';
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

#print STDERR "RET $ret\n";
exit($ret);
