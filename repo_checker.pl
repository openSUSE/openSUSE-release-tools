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
my @directories = split(/\,/, shift @ARGV);
my %toignore;
my %whitelist;
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
    elsif ( $switch eq "-w" ) {
        %whitelist = map { $_ => 1 } split(/\,/, shift @ARGV);
    }
    else {
        print "read the source luke: $switch ? \n";
        exit(1);
    }
}

my %targets;

sub write_package {
    my ($package, $packages_fd, $written_names) = @_;

    my $name = basename($package);
    if ($name =~ m/^[a-z0-9]{32}-/) { # repo cache
       $name =~ s,^[^-]+-(.*)\.rpm,$1,;
    } else {
       $name =~ s,^(.*)-[^-]+-[^-]+.rpm,$1,;
    }

    if ( defined $written_names->{$name} ) {
        return;
    }
    $written_names->{$name} = $package;

    my $out = CreatePackageDescr::package_snippet($package);
    if ($out eq "" || $out =~ m/=Pkg:    /) {
        print STDERR "ERROR: empty package snippet for: $name\n";
        exit(126);
    }
    print $packages_fd $out;
    return $name;
}

my @rpms;
my $tmpdir = tempdir( "repochecker-XXXXXXX", TMPDIR => 1, CLEANUP => 1 );
my $pfile = $tmpdir . "/packages";
open( my $packages_fd, ">", $pfile ) || die 'can not open';
print $packages_fd "=Ver: 2.0\n";

my $written_names = {};
my $first_layer = 1;

foreach my $directory (@directories) {
    @rpms = glob("$directory/*.rpm");
    foreach my $package (@rpms) {
        my $name = write_package( $package, $packages_fd, $written_names );
        if ($first_layer && $name && !exists($whitelist{$name})) {
            $targets{$name} = 1;
        }
    }

    if ($first_layer) {
        foreach my $key (keys %toignore) {
            if (!defined($written_names->{$key})) {
                $written_names->{$key} = "simulate overridden";
            }
        }
        $first_layer = 0;
    }
}

close($packages_fd);

my $error_file = $tmpdir . "/error_file";
open(INSTALL, "/usr/bin/installcheck $arch $pfile 2> $error_file |")
  || die 'exec installcheck';
my $inc = 0;
while (<INSTALL>) {
    chomp;

    next if (/^unknown line:.*Flx/);
    if ($_ =~ /^[^ ]/) {
        $inc = 0;
    }
    if ( $_ =~ /^can't install (.*)-[^-]+-[^-]+:$/ ) {
        if ( defined $targets{$1} ) {
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

my $cmd = sprintf( "perl %s/findfileconflicts $pfile", dirname($0) );
open(CONFLICTS, "$cmd 2> $error_file |") || die 'exec fileconflicts';
$inc = 0;
while (<CONFLICTS>) {
    chomp;

    if ($_ =~ /^[^ ]/) {
        $inc = 0;
    }
    if ( $_ =~ /^found conflict of (.*)-[^-]+-[^-]+ with (.*)-[^-]+-[^-]+:$/ ) {
        if ( defined $targets{$1} || defined $targets{$2} ) {
            $inc = 1;
            $ret = 1;
        }
    }
    if ($inc) {
        print "$_\n";
    }
}
close(CONFLICTS);

open(ERROR, '<', $error_file);
while (<ERROR>) {
    chomp;
    print STDERR "$_\n";
    $ret = 1;
}
close(ERROR);

exit($ret);
