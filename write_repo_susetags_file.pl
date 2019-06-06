#! /usr/bin/perl -w

use strict;
use File::Basename;

BEGIN {
    my ($wd) = $0 =~ m-(.*)/-;
    $wd ||= '.';
    unshift @INC, $wd;
}

require CreatePackageDescr;

die "Usage: $0 <output directory> <input directories...>" if scalar(@ARGV) < 2;

my $output_directory = shift @ARGV;
my @directories      = @ARGV;

sub write_package {
    my ($package, $packages_fd, $directory, $written_names, $sources) = @_;

    my $name = basename($package);
    if ($name =~ m/^[a-z0-9]{32}-/) {    # repo cache
        $name =~ s,^[^-]+-(.*)\.rpm,$1,;
    } else {
        $name =~ s,^(.*)-[^-]+-[^-]+.rpm,$1,;
    }

    if (defined $written_names->{$name}) {
        return;
    }
    $written_names->{$name} = $directory;

    my $out = CreatePackageDescr::package_snippet($package);
    if ($out eq "" || $out =~ m/=Pkg:    /) {
        print STDERR "ERROR: empty package snippet for: $name\n";
        exit(126);
    }
    if ($out =~ m/=Src: ([^ ]*)/) {
        $sources->{$name} = $1;
    }
    print $packages_fd $out;
    return $name;
}

open(my $packages_fd, ">", "$output_directory/packages") || die 'can not open';
print $packages_fd "=Ver: 2.0\n";

my %written_names;
my %sources;

for my $directory (@directories) {
    my @rpms = glob("$directory/*.rpm");
    write_package($_, $packages_fd, $directory, \%written_names, \%sources) for @rpms;
}

close($packages_fd);

# turn around key->value
my %per_directory;
for my $name (keys %written_names) {
    $per_directory{$written_names{$name}} ||= [];
    push(@{$per_directory{$written_names{$name}}}, $name);
}

open(my $yaml_fd, ">", "$output_directory/catalog.yml") || die 'can not open';
for my $directory (@directories) {
    next unless defined($per_directory{$directory});
    print $yaml_fd "$directory:\n";
    for my $name (@{$per_directory{$directory}}) {
        my $source = $sources{$name} || 'unknown';
        print $yaml_fd "  '$name': '$source'\n";
    }
}
close($yaml_fd);
