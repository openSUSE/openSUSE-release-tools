use XML::Simple;
use LWP::UserAgent;
use Data::Dumper;
use Algorithm::Diff qw(diff sdiff);

my $ua = LWP::UserAgent->new;
$ua->agent("generate-reminder.pl");
$ua->timeout(180);
$ua->max_size(100000000);
my $baseurl = "https://build.opensuse.org";

my %ringed;
my %toignore;
for my $i (qw(kernel-syms kernel-xen glibc.i686 openSUSE-release kernel-desktop kernel-default kernel-pae Test-DVD-x86_64 kiwi-image-livecd-gnome.x86_64 kiwi-image-livecd-kde.x86_64 package-lists-kde.x86_64 package-lists-gnome.x86_64)) {
  $toignore{$i} = 1;
}

sub fetch_api($) {
  my $route = shift;
  my $mywork = $ua->get("$baseurl$route");
  unless ($mywork->is_success) { die "$route - " . $mywork->status_line; }

  return $mywork->decoded_content;
}

sub fetch_prj($) {
  my $prj = shift;
  
  my $packages = XMLin(fetch_api("/status/project/$prj"))->{package};
  my %ret;
  for my $p (keys %${packages}) {
    next if defined $toignore{$p} || $p =~ /AGGR/;
    $ret{$p} = $packages->{$p}->{verifymd5} || $packages->{$p}->{srcmd5};
  }
  return \%ret;
}

my $fact = fetch_prj('openSUSE:Factory');

sub check_ring($) {
  my $ringprj = shift;

my $ring = fetch_prj($ringprj);

PACKAGE: for my $p (keys %${ring}) {
	#print "checking $ringprj $p $ring->{$p} $fact->{$p}\n";
  if ($ringed{$p}) {
    print "osc rdelete $ringprj $p -m 'in two rings'\n";
    next;
  }
  $ringed{$p} = $ring->{$p};
  if ($ring->{$p} ne $fact->{$p}) {
    
    eval {
    my $fxml = XMLin(fetch_api("/source/openSUSE:Factory/$p?view=info"));
    my $rxml = XMLin(fetch_api("/source/$ringprj/$p?view=info"));

    next if ($fxml->{verifymd5} eq $rxml->{verifymd5});
    };
    if ($@) {
      print "# $@";
      print "osc rdelete -m 'gone away' $ringprj $p\n";
      next;
    }

    eval {
    my @ftext = split(/\n/, fetch_api("/source/openSUSE:Factory/$p/$p.changes?expand=1"));
    my @rtext = split(/\n/, fetch_api("/source/$ringprj/$p/$p.changes?expand=1"));
  
    my @d = sdiff( \@ftext, \@rtext );
    if (!length(@d)) {
     print "# no changes diff in $p\n";
     next;
    }
    # first chunk
    for my $d (@d) {
      my @e = @$d;
      my $c = shift @e;
      next if ($c eq 'u');
      if ($c eq '+') {
        print "# diff is +: osc rdiff openSUSE:Factory $p $ringprj $p - " . join('', @e) . "\n";
        next PACKAGE;
      }
    }
    };
    warn $@ if $@;
    #system("osc rdiff openSUSE:Factory $p openSUSE:Factory:Core $p");
    print "osc linkpac -f -c openSUSE:Factory $p $ringprj\n";
  }
}
}

check_ring("openSUSE:Factory:Build");
check_ring("openSUSE:Factory:Core");
check_ring("openSUSE:Factory:MainDesktops");
check_ring("openSUSE:Factory:DVD");

sub check_package($$$) {
  my $package = shift; 
  my $spkg = shift;
  my $tpkg = shift;

  my $p1 = XMLin(fetch_api("/source/$spkg/$package?view=info"));
  my $p2 = XMLin(fetch_api("/source/$tpkg/$package?view=info"));

  return if ($p1->{verifymd5} eq $p2->{verifymd5});
  print "osc linkpac -f -r $p1->{srcmd5} $spkg $package $tpkg\n";
}

check_package('Test-DVD-x86_64', 'openSUSE:Factory', 'openSUSE:Factory:Core');
check_package('kiwi-image-livecd-gnome.x86_64', 'openSUSE:Factory:Live', 'openSUSE:Factory:MainDesktops');
check_package('kiwi-image-livecd-kde.x86_64', 'openSUSE:Factory:Live', 'openSUSE:Factory:MainDesktops');
check_package('package-lists-gnome.x86_64', 'openSUSE:Factory:Live', 'openSUSE:Factory:MainDesktops');
check_package('package-lists-kde.x86_64', 'openSUSE:Factory:Live', 'openSUSE:Factory:MainDesktops');

