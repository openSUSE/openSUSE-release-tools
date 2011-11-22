#! /usr/bin/perl

use File::Basename;

my $old = $ARGV[0];
my $dir = $ARGV[1];
my $bname = basename($dir);

if (! -f "$dir/$bname.changes") {
  print "A $bname.changes is missing. Packages submitted as FooBar, need to have a FooBar.changes file with a format created by osc vc\n";
  exit(1);
}

if (! -f "$dir/$bname.spec") {
  print "A $bname.spec is missing. Packages submitted as FooBar, need to have a FooBar.spec file\n";
  exit(1);
}

open(SPEC, "$dir/$bname.spec");
my $spec = join("", <SPEC>);
close(SPEC);
my $sname = '';
if ($spec =~ m/\nName:\s*(\w+)\s*/) {
  $sname = $1;
}

if ($bname ne $sname) {
  print "$bname.spec needs to contain Name: $bname, found '$sname'\n";
  exit(1);
}

if ($spec =~ m/\nVendor:/) {
  print "$bname.spec contains a Vendor line, this is forbidden.\n";
  exit(1);
}

if (-f "$old/$bname.changes") {
    if (!system("cmp -s $old/$bname.changes $dir/$bname.changes")) {
	print "$bname.changes didn't change. Please use osc vc\n";
	exit(1);
    }
}

if ($spec !~ m/\n%changelog\s/) {
    print "$bname.spec does not contain a %changelog line. We don't want a changelog in the spec file, but the %changelog section needs to be present\n";
    exit(1);
}

if ($spec !~ m/#\s+Copyright\s/) {
    print "$bname.spec does not appear to contain a Copyright comment. Please stick to the format\n\n";
    print "# Copyright (c) 2011 Stephan Kulow\n\n";
    print "or use osc service localrun format_spec_file\n";
}

exit(1) if system("/work/src/bin/check_if_valid_source_dir --batchmode --dest _old $dir < /dev/null 2>&1 | grep -v '##ASK'") != 0;
 
