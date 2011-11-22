#! /usr/bin/perl

use File::Basename;

my $dir = $ARGV[0];
my $bname = basename($dir);

if (! -f "$dir/$bname.changes") {
  print "A $bname.changes is missing. Packages submitted as FooBar, need to have a FooBar.changes file with a format created by osc vc\n";
  exit(1);
}

if (! -f "$dir/$bname.spec") {
  print "A $bname.spec is missing. Packages submitted as FooBar, need to have a FooBar.spec file\n";
  exit(1);
}

open(SPEC, "grep ^Name: $dir/$bname.spec| head -n 1");
my $line = <SPEC>;
close(SPEC);
chomp $line;
$line =~ s,Name:\s*,,;
if ($bname ne $line) {
  print "$bname.spec needs to contain Name: $bname\n";
  exit(1);
}
close(SPEC);

exit(1) if system("/work/src/bin/check_if_valid_source_dir --batchmode --dest _old $dir < /dev/null 2>&1 | grep -v '##ASK'") != 0;
 
