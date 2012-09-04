#! /usr/bin/perl

use File::Basename;
use File::Temp qw/ :mktemp  /;
use XML::Simple;
use Data::Dumper;
use Cwd;

my $old = $ARGV[0];
my $dir = $ARGV[1];
my $bname = basename($dir);

if (-f "$dir/_service") {
    my $service = XMLin("$dir/_service", ForceArray => [ 'service' ]);
    while( my ($name, $s) = each %{$service->{service}} ) {
        my $mode = $s->{mode} || '';
        next if ($mode eq "localonly" || $mode eq "disabled");
        print "Services are only allowed if they are mode='localonly'. Please change the mode of $name and use osc service localrun\n";
        exit(1);
    }
}

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

if ($spec !~ m/#\s+Copyright\s/) {
    print "$bname.spec does not appear to contain a Copyright comment. Please stick to the format\n\n";
    print "# Copyright (c) 2011 Stephan Kulow\n\n";
    print "or use osc service localrun format_spec_file\n";
    exit(1);
}

if ($spec =~ m/\nVendor:/) {
  print "$bname.spec contains a Vendor line, this is forbidden.\n";
  exit(1);
}

foreach my $file (glob("$dir/_service:*")) {
   $file=basename($file);
   print "Found _service generate file $file in checkout. Please clean this up first.";
   exit(1);
}

# Check that we have for each spec file a changes file - and that at least one
# contains changes
my $changes_updated = 0;
for my $spec (glob ("$dir/*.spec")) {
    $changes = basename ($spec);
    $changes =~ s/\.spec$/.changes/;
    if (! -f "$dir/$changes") {
	print "A $changes is missing. Packages submitted as FooBar, need to have a FooBar.changes file with a format created by osc vc\n";
	exit(1);
    }
    if (-f "$old/$changes") {
	if (system("cmp -s $old/$changes $dir/$changes")) {
	    $changes_updated = 1;
	}
    } else { # a new file is an update too
	$changes_updated = 1;
    }
}
if (!$changes_updated) {
    print "No changelog. Please use 'osc vc' to update the changes file(s).\n";
    exit(1); 
}

if ($spec !~ m/\n%changelog\s/ && $spec != m/\n%changelog$/) {
    print "$bname.spec does not contain a %changelog line. We don't want a changelog in the spec file, but the %changelog section needs to be present\n";
    exit(1);
}

if ($spec !~ m/(#[^\n]*license)/i) {
    print "$bname.spec does not appear to have a license, the file needs to contain a free software license\n";
    print "Suggestion: use \"osc service localrun format_spec_file\" to get our default license or\n";
    print "the minimal license:\n\n";
    print "# This file is under MIT license\n";
    exit(1);
}

my $checkivsd = `/work/src/bin/check_if_valid_source_dir --batchmode --dest _old $dir < /dev/null 2>&1`;
if ($?) {
    print "Source validator failed. Try \"osc service localrun source_validator\"\n";
    print $checkivsd;
    print "\n";
    exit(1);
}

if (-d "_old") {
    chdir("_old") || die "chdir _old failed";
    my %thash = ();
    my %rhash = ();
    for my $spec (glob("*.spec")) {
	open(PIPE, "grep '^Source' $spec |");
	while (<PIPE>) {
	    chomp;
	    s/^Source[0-9]*\s*:\s*//;
	    $thash{$_} = 1;
	}
	close(PIPE);
    }
    chdir("../$dir") || die "chdir failed";
    for my $spec (glob("*.spec")) {
	open(OSPEC, "$spec");
	open(NSPEC, ">$spec.new");
	while (<OSPEC>) {
	    chomp;
	    if (m/^Source/) {
		my $line = $_;
		$line =~ s/^(Source[0-9]*)\s*:\s*//;
		my $prefix = $1;
		if (defined $thash{$line}) {
		    my $file = $line;
		    my $bname = basename($file);
		    print NSPEC "$prefix: $bname\n";
		} else {
		    print NSPEC "$_\n";
		}
	    } else {
		print NSPEC "$_\n";
	    }
	}
	close(OSPEC);
	close(NSPEC);
	rename("$spec.new", "$spec") || die "rename failed";
    }
    chdir("..");
}

my $odir = getcwd;
my $tmpdir = mkdtemp("/tmp/obs-XXXXXXX");
chdir($dir);
if (system("/usr/lib/obs/service/download_files","--enforceupstream", "yes", "--enforcelocal", "yes", "--outdir", $tmpdir)) {
    print "Source URLs are not valid. Try \"osc service localrun download_files\"\n";
    exit(1);
}
chdir($odir);

foreach my $rpmlint (glob("$dir/*rpmlintrc")) {
    open(RPMLINTRC, $rpmlint);
    while ( <RPMLINTRC> ) {
	if ( m/^\s*setBadness/ ) {
	    print "For Factory submissions, you can not use setBadness. Use filters in $rpmlint\n";
	    exit(1);
	}
    }
}
