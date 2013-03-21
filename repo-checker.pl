#! /usr/bin/perl

use File::Basename;
use File::Temp qw/ tempdir  /;
use XML::Simple;
use Data::Dumper;
use Cwd;

my $dir = $ARGV[0];

if (! -f "$dir/rpmlint.log") {
  print "Couldn't find a rpmlint.log in the build results. This is mandatory\n";
  exit(1);
}

open(GREP, "grep 'W:.*invalid-lcense ' $dir/rpmlint.log |");
while ( <GREP> ) {
  print "Found rpmlint warning: ";
  print $_;
  exit(1);
}

exit(0);

