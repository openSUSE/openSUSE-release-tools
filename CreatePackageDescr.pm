package CreatePackageDescr;

BEGIN {
    unshift @INC, "/usr/lib/build/Build";
}

use File::Basename;

use Rpm;
use Fcntl;

sub package_snippet($) {

    my $package = shift;

    my $cachedir  = dirname($package) . "/.cache/";
    my $cachefile = $cachedir . basename($package);

    my $out = '';
    if ( -f $cachefile ) {
        open( C, $cachefile ) || die "no cache for $package";
        while (<C>) {
            $out .= $_;
        }
        close(C);
        return $out;
    }

    # RPMTAG_FILEMODES            = 1030, /* h[] */
    # RPMTAG_FILEFLAGS            = 1037, /* i[] */
    # RPMTAG_FILEUSERNAME         = 1039, /* s[] */
    # RPMTAG_FILEGROUPNAME        = 1040, /* s[] */

    my %qq = Build::Rpm::rpmq(
        $package,
        qw{NAME VERSION RELEASE ARCH OLDFILENAMES DIRNAMES BASENAMES DIRINDEXES 1030 1037 1039 1040
          1047 1112 1113 1049 1048 1050 1090 1114 1115 1054 1053 1055 1036
          }
    );

    my $name = $qq{'NAME'}[0];

    Build::Rpm::add_flagsvers( \%qq, 1049, 1048, 1050 );    # requires
    Build::Rpm::add_flagsvers( \%qq, 1047, 1112, 1113 );    # provides
    Build::Rpm::add_flagsvers( \%qq, 1090, 1114, 1115 );    # obsoletes
    Build::Rpm::add_flagsvers( \%qq, 1054, 1053, 1055 );    # conflicts

    $out .= sprintf( "=Pkg: %s %s %s %s\n",
        $name, $qq{'VERSION'}[0], $qq{'RELEASE'}[0], $qq{'ARCH'}[0] );
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
        next if ( $prv =~ m/^rpmlib/ );
        next
          if ( $name eq "libqmmp0-plugin-mplayer"
            && $prv eq "/usr/bin/mplayer" );
        next if ( $prv eq "this-is-only-for-build-envs" );
        next
          if ( $name eq "installation-images-debuginfodeps"
            && $prv =~ m/debuginfo.build/ );
        next
          if ( $name eq "installation-images-debuginfodeps-openSUSE"
            && $prv =~ m/debuginfo.build/ );
        $out .= "$prv\n";
    }
    $out .= "-Req:\n";
    $out .= "+Obs:\n";
    foreach my $prv ( @{ $qq{1090} || [] } ) {
        $out .= "$prv\n";
    }
    $out .= "-Obs:\n";

    mkdir($cachedir);
    open( C, '>', $cachefile ) || die "can't write $cachefile";
    print C $out;
    close(C);

    return $out;
}

1;
