#! /usr/bin/perl

use File::Basename;
use File::Temp qw/ tempdir  /;
use Cwd;
use Text::Diff;
BEGIN {
    unshift @INC, ($::ENV{'BUILD_DIR'} || '/usr/lib/build');
}
use Build;

my $ret = 0;

my $old = $ARGV[0];
my $dir = $ARGV[1];
my $bname = basename($dir);

my $odir = getcwd();

if (-d "$old") {

    chdir($old) || die "chdir $old failed";
    my $cf = Build::read_config("x86_64", "/usr/lib/build/configs/default.conf");

    my %thash = ();
    for my $spec (glob("*.spec")) {
        my $ps = Build::Rpm::parse($cf, $spec);

        while (my ($k, $v) = each %$ps) {
            if ($k =~ m/^source/) {
                $thash{$v} = 1;
            }
        }
    }
   
    chdir($odir) || die "chdir $odir failed";
    chdir($dir) || die "chdir $dir failed";
    for my $spec (glob("*.spec")) {
        my $ps = Build::Rpm::parse($cf, $spec);
        open(OSPEC, "$spec");
        open(NSPEC, ">$spec.new");
        while (<OSPEC>) {
            chomp;
            if (m/^Source/) {
                my $line = $_;
                $line =~ s/^(Source[0-9]*)\s*:\s*//;
                my $prefix = $1;
                my $parsedline = $ps->{lc $prefix};
                if (defined $thash{$parsedline}) {
                    my $file = $line;
                    my $bname = basename($file);
                    print NSPEC "$prefix: $bname\n";
                }
                else {
                    print NSPEC "$_\n";
                }
            }
            else {
                print NSPEC "$_\n";
            }
        }
        close(OSPEC);
        close(NSPEC);
        system(("cp", "$spec", "$spec.beforeurlstrip"));
        rename("$spec.new", "$spec") || die "rename failed";
    }

}

my $tmpdir = tempdir("obs-XXXXXXX", TMPDIR => 1, CLEANUP => 1);
chdir($odir) || die "chdir $odir failed";
chdir($dir) || die "chdir $dir failed";
if (system("/usr/lib/obs/service/download_files","--enforceupstream", "yes", "--enforcelocal", "yes", "--outdir", $tmpdir)) {
    print "Source URLs are not valid. Try \"osc service runall download_files\".\n";
    $ret = 2;
}

exit($ret);
