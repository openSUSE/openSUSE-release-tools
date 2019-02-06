package CreatePackageDescr;

BEGIN {
    unshift @INC, "/usr/lib/build/Build";
}

use File::Basename;

use Rpm;
use Fcntl qw/:flock/;

sub package_snippet($) {

    my $package = shift;

    my $cachedir  = dirname($package) . "/.cache/";
    my $cachefile = $cachedir . basename($package);

    my $out = '';
    if ( -f $cachefile ) {
        open( C, '<', $cachefile ) || die "no cache for $package";
        flock(C, LOCK_SH) or die "failed to lock $cachefile: $!\n";
        while (<C>) {
            $out .= $_;
        }
        close(C);

        # Detect corrupt cache file and rebuild.
        if ($out eq "" || $out =~ m/=Pkg:    /) {
            unlink($cachefile);
            $out = '';
        }
        else {
            return $out;
        }
    }

    # RPMTAG_FILEMODES            = 1030, /* h[] */
    # RPMTAG_FILEFLAGS            = 1037, /* i[] */
    # RPMTAG_FILEUSERNAME         = 1039, /* s[] */
    # RPMTAG_FILEGROUPNAME        = 1040, /* s[] */

    my %qq = Build::Rpm::rpmq(
        $package,
        qw{NAME VERSION RELEASE ARCH OLDFILENAMES DIRNAMES BASENAMES DIRINDEXES 1030 1037 1039 1040
          1047 1112 1113 1049 1048 1050 1090 1114 1115 1054 1053 1055 1036 5046 5047 5048 5049 5050 5051
          5052 5053 5054 5055 5056 5057 1156 1158 1157 1159 1161 1160
          }
    );

    if (!exists $qq{'NAME'}[0]) {
        print STDERR "corrupt rpm: $package\n";
        unlink($package);
        return $out; # Needs to be re-mirrored, so return blank to trigger error.
    }

    my $name = $qq{'NAME'}[0];

    Build::Rpm::add_flagsvers( \%qq, 1049, 1048, 1050 );    # requires
    Build::Rpm::add_flagsvers( \%qq, 1047, 1112, 1113 );    # provides
    Build::Rpm::add_flagsvers( \%qq, 1090, 1114, 1115 );    # obsoletes
    Build::Rpm::add_flagsvers( \%qq, 1054, 1053, 1055 );    # conflicts

   Build::Rpm::add_flagsvers(\%qq, 1156, 1158, 1157) if $qq{1156}; # oldsuggests
   Build::Rpm::add_flagsvers(\%qq, 1159, 1161, 1160) if $qq{1159}; # oldenhances

   Build::Rpm::add_flagsvers(\%qq, 5046, 5048, 5047) if $qq{5046}; # recommends
   Build::Rpm::add_flagsvers(\%qq, 5049, 5051, 5050) if $qq{5049}; # suggests
   Build::Rpm::add_flagsvers(\%qq, 5052, 5054, 5053) if $qq{5052}; # supplements
   Build::Rpm::add_flagsvers(\%qq, 5055, 5057, 5056) if $qq{5055}; # enhances

    $arch = $qq{'ARCH'}[0];
    # some packages are more equal than others
    $arch = 'i586' if $arch eq 'i686';
    $out .= sprintf( "=Pkg: %s %s %s %s\n",
        $name, $qq{'VERSION'}[0], $qq{'RELEASE'}[0], $arch );
    $out .= "+Flx:\n";
    my @modes      = @{ $qq{1030}       || [] };
    my @basenames  = @{ $qq{BASENAMES}  || [] };
    my @dirs       = @{ $qq{DIRNAMES}   || [] };
    my @dirindexes = @{ $qq{DIRINDEXES} || [] };
    my @users      = @{ $qq{1039}       || [] };
    my @groups     = @{ $qq{1040}       || [] };
    my @flags      = @{ $qq{1037}       || [] };
    my @linktos    = @{ $qq{1036}       || [] };

    my @xprvs;

    foreach my $bname (@basenames) {
        my $mode   = shift @modes;
        my $di     = shift @dirindexes;
        my $user   = shift @users;
        my $group  = shift @groups;
        my $flag   = shift @flags;
        my $linkto = shift @linktos;

        my $filename = $dirs[$di] . $bname;
        my $fs       = $filename;
        if ( Fcntl::S_ISLNK($mode) ) {
            $fs = "$filename -> $linkto";
        }
        $out .= sprintf "%o %o %s:%s %s\n", $mode, $flag, $user, $group, $fs;
        if (   $filename =~ /^\/etc\//
            || $filename =~ /bin\//
            || $filename eq "/usr/lib/sendmail" )
        {
            push @xprvs, $filename;
        }

    }
    $out .= "-Flx:\n";
    $out .= "+Prv:\n";
    foreach my $prv ( @{ $qq{1047} || [] } ) {
        $out .= "$prv\n";
    }
    foreach my $prv (@xprvs) {
        $out .= "$prv\n";
    }
    $out .= "-Prv:\n";
    $out .= "+Con:\n";
    foreach my $prv ( @{ $qq{1054} || [] } ) {
        $out .= "$prv\n";
    }
    $out .= "-Con:\n";
    $out .= "+Req:\n";
    foreach my $prv ( @{ $qq{1049} || [] } ) {
        next if ( $prv eq "this-is-only-for-build-envs" );
        # Completely disgusting, but maintainers have no interest in fixing,
        # see #1153 for more details.
        next
          if ( $name =~ "^installation-images-debuginfodeps.*"
            && $prv =~ m/debuginfo.build/ );
        $out .= "$prv\n";
    }
    $out .= "-Req:\n";

    $out .= "+Obs:\n";
    foreach my $prv ( @{ $qq{1090} || [] } ) {
        $out .= "$prv\n";
    }
    $out .= "-Obs:\n";

    $out .= "+Rec:\n";
    foreach my $prv ( @{ $qq{5046} || [] } ) {
        # ignore boolean dependencies
        next if $prv =~ m/^\(/;
        $out .= "$prv\n";
    }
    $out .= "-Rec:\n";

    $out .= "+Sup:\n";
    foreach my $prv ( @{ $qq{5052} || [] } ) {
        $out .= "$prv\n";
    }
    $out .= "-Sup:\n";

    $out .= "+Enh:\n";
    foreach my $prv ( @{ $qq{5055} || [] } ) {
        $out .= "$prv\n";
    }
    $out .= "-Enh:\n";

    $out .= "+Sug:\n";
    foreach my $prv ( @{ $qq{5049} || [] } ) {
        $out .= "$prv\n";
    }
    $out .= "-Sug:\n";

    mkdir($cachedir);
    open(C, '>', $cachefile) || die "can't open $cachefile";
    flock(C, LOCK_EX) or die "failed to lock $cachefile: $!\n";
    seek(C, 0, 0); truncate(C, 0);
    print C $out;
    close(C);

    return $out;
}

1;
