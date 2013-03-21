#! /usr/bin/perl -w

BEGIN {
  unshift @INC, "/usr/lib/build/Build";
}

use File::Basename;
use File::Temp qw/ tempdir  /;
use XML::Simple;
use Data::Dumper;
use Cwd;
use Rpm;

use strict;

my $dir = $ARGV[0];
my %toignore;
foreach my $name (split(/,/, $ARGV[1])) {
   $toignore{$name} = 1;
}

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

my @rpms = glob("~/factory-repo/*.rpm");
open(PACKAGES, ">", $ENV{'HOME'} . "/packages") || die 'can not open';
print PACKAGES "=Ver: 2.0\n";

sub write_package($$) 
{
  my $package = shift;
  my $ignore = shift;

  # RPMTAG_FILEMODES            = 1030, /* h[] */
  # RPMTAG_FILEFLAGS            = 1037, /* i[] */
  # RPMTAG_FILEUSERNAME         = 1039, /* s[] */
  # RPMTAG_FILEGROUPNAME        = 1040, /* s[] */
    
  my %qq = Build::Rpm::rpmq("$package", qw{NAME VERSION RELEASE ARCH OLDFILENAMES DIRNAMES BASENAMES DIRINDEXES 1030 1037 1039 1040
			   1047 1112 1113 1049 1048 1050 1090 1114 1115 1054 1053 1055
					});
  if ($ignore && defined $toignore{$qq{'NAME'}[0]}) {
      next;
  }

  Build::Rpm::add_flagsvers(\%qq, 1049, 1048, 1050); # requires
  Build::Rpm::add_flagsvers(\%qq, 1047, 1112, 1113); # provides
  Build::Rpm::add_flagsvers(\%qq, 1090, 1114, 1115); # obsoletes
  Build::Rpm::add_flagsvers(\%qq, 1054, 1053, 1055); # conflicts
  
  #print Dumper(\%qq);
  printf PACKAGES "=Pkg: %s %s %s %s\n", $qq{'NAME'}[0], $qq{'VERSION'}[0], $qq{'RELEASE'}[0], $qq{'ARCH'}[0];
  print  PACKAGES "+Flx:\n";
  my @modes = @{$qq{1030} || []};
  my @basenames = @{$qq{BASENAMES} || []};
  my @dirs = @{$qq{DIRNAMES} || []};
  my @dirindexes = @{$qq{DIRINDEXES} || []};
  my @users = @{$qq{1039} || []};
  my @groups = @{$qq{1040} || []};
  my @flags = @{$qq{1037} || []};

  my @xprvs;

  foreach my $bname (@basenames) {
    my $mode = shift @modes;
    my $di = shift @dirindexes;
    my $user = shift @users;
    my $group = shift @groups;
    my $flag = shift @flags;
    
    my $filename = $dirs[$di] . $bname;
    printf PACKAGES "%o %o %s:%s %s\n", $mode, $flag, $user, $group, $filename;
    if ( $filename =~ /^\/etc\// || $filename =~ /bin\// || $filename eq "/usr/lib/sendmail" ) {
      push @xprvs, $filename;
    }
  }
  print PACKAGES "-Flx:\n";
  print PACKAGES "+Prv:\n";
  foreach my $prv (@{$qq{1047} || []}) {
    print PACKAGES "$prv\n";
  }
  foreach my $prv (@xprvs) {
    print PACKAGES "$prv\n";
  }
  print PACKAGES "-Prv:\n";
  print PACKAGES "+Con:\n";
  foreach my $prv (@{$qq{1054} || []}) {
    print  PACKAGES "$prv\n";
  }
  print PACKAGES "-Con:\n";
  print PACKAGES "+Req:\n";
  foreach my $prv (@{$qq{1049} || []}) {
    print PACKAGES "$prv\n" unless $prv =~ m/^rpmlib/;
  }
  print PACKAGES "-Req:\n";
  print PACKAGES "+Obs:\n";
  foreach my $prv (@{$qq{1090} || []}) {
    print PACKAGES "$prv\n";
  }
  print PACKAGES "-Obs:\n";

}

foreach my $package (@rpms) {
    write_package(1, $package);
}

@rpms = glob("$dir/*.rpm");
foreach my $package (@rpms) {
    write_package(0, $package);
}

close(PACKAGES);

exit(0);
