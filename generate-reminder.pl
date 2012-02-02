#! /usr/bin/perl

require LWP::UserAgent;
use JSON;
use POSIX;
use Carp::Always;
use Data::Dumper;

my $user = $ARGV[0];
my $tproject = "openSUSE:Factory";

sub fetch_user_infos($)
{
    my ($user) = @_;

    if (-f "reports/$user") {
	open( my $fh, '<', "reports/$user" );
	my $json_text   = <$fh>;
	my $st = decode_json( $json_text );
	close($fh);
	return ($st->{'mywork'}, $st->{projstat});
    }

    my $ua = LWP::UserAgent->new;
    $ua->timeout(15);
    $ua->default_header("Accept" => "application/json");
    $mywork = $ua->get("https://build.opensuse.org/stage/home/my_work?user=$user");
    unless ($mywork->is_success) { die $mywork->status_line; }

    $mywork = from_json( $mywork->decoded_content, { utf8 => 1 });

    my $url = "https://build.opensuse.org/stage/project/status?project=$tproject&ignore_pending=0";
    $url .= "&limit_to_fails=false&limit_to_old=false&include_versions=true&filter_for_user=$user";
    $projstat = $ua->get($url);
    die $projstat->status_line unless ($projstat->is_success);
    $projstat = from_json( $projstat->decoded_content, { utf8 => 1 });

    my %st = ();
    $st->{'mywork'} = $mywork;
    $st->{'projstat'} = $projstat;
    open(my $fh, '>', "reports/$user");
    print $fh to_json($st);
    close $fh;
    return ($mywork, $projstat);
}

($mywork, $projstat) = fetch_user_infos($user);

#print to_json($mywork, {pretty => 1 });
#print to_json($projstat, {pretty => 1});

my %projects;
for my $package (@{$projstat}) {
    $projects{$package->{develproject}} ||= [];
    push($projects{$package->{develproject}}, $package);
}

my $baseurl = "https://build.opensuse.org/stage";

sub time_distance($)
{
    my ($past) = @_;
    my $minutes = (time() - $past) / 60;

    if ($minutes < 1440) {
	return "less than 1 day";
    }
    if ($minutes < 2520) {
	return "1 day";
    }
    if ($minutes < 43200) {
	my $days = floor($minutes / 1440. + 0.5);
	return "$days days";
    }

    my $months = floor($minutes / 43200. + 0.5);
    if ($months == 1) {
	return "1 month";
    } else {
	return "$months months";
    }
}

my %requests_to_ignore;
my %reviews_by;

for my $request (@{$mywork->{review}}) {
    # stupid ruby... :)
    $request = $request->{request};
    my $reviews = $request->{review};
    $reviews = [$reviews] if (ref($reviews) eq "HASH");
    for my $review (@{$reviews}) {
	next if ($review->{state} ne 'new');
	if ($review->{by_user} eq $user) {
	    print "Request $request->{id} is waiting for your review!\n";
	    $requests_to_ignore{$request->{id}} = 1;
	    next;
	}
	# we ignore by_group for now
	if ($review->{by_group} ) {
	    $requests_to_ignore{$request->{id}} = 1;
	    next;
	}
	if ($review->{by_project}) {
	    my $bproj = $review->{by_project};
	    my $bpack = $review->{by_package};
	    $reviews_by{$bproj + "/" + $bpack} = $request;
	    next;
	}
    }
}

for my $project (sort(keys %projects)) {
    my @lines;

    for my $package (@{$projects{$project}}) {
	next if @{$package->{requests_from}};

	# do not show version information if there is more important stuff
	my $showversion = 1;
	my $ignorechanges = 0;

	my $key = "$project/$package->{name}";
	if ($reviews_by{$key}) {
	    my $r = $reviews_by{$key};
	    push(@lines, "Request $r->{id} for $package->{name} waits for review!\n");
	    delete $reviews_by{$key};
	}

	if ($package->{firstfail} && $package->{develfirstfail}) {
	    my $fail = time_distance($package->{firstfail});
	    my $comment = $package->{failedcomment};
	    push(@lines, "  $package->{name} fails for $fail ($comment)\n");
	    $ignorechanges = 1;
	}

	for my $problem (sort @{$package->{problems}}) {
	    if ($problem eq 'different_changes') {
		my $url = "$baseurl/package/rdiff?opackage=$package->{name}&oproject=$tproject&package=$package->{develpackage}&project=$package->{develproject}";
		if ($ignorechanges == 0) {
		    push(@lines, "  $package->{name} has unsubmitted changes.\n");
		    $showversion = 0;
		}
	    } elsif ($problem eq 'currently_declined') {
		push(@lines, "  $package->{name} was declined in request $package->{currently_declined}. Please check the reason.\n");
		$showversion = 0;
	    } 
	}
	
	for my $request (@{$package->{requests_to}}) {
	    push(@lines, "  $package->{name} has pending request $request\n");
	    $requests_to_ignore{$request} = 1;
	    $showversion = 0;
	}

	if ($showversion && $package->{upstream_version}) {
	    push(@lines, "  $package->{name} has new upstream version $package->{upstream_version} available.\n");
	}
    }
    if (@lines) {
	print "PROJ $project\n";
	print join('',@lines);
    }
}

sub explain_request($$)
{
    my ($request, $list) = @_;
    next if (defined($requests_to_ignore{$request->{id}}));
    if ($request->{action} &&  $request->{action}->{type} eq "submit") {
	my $source = $request->{action}->{source};
	my $target = $request->{action}->{target};
	$list->{int($request->{id})} = "Submit request $request->{id} from $source->{project}/$source->{package} to $target->{project}\n";
    } elsif ($request->{action} &&  $request->{action}->{type} eq "delete") {
	my $target = $request->{action}->{target};
	$list->{int($request->{id})} = "Delete request $request->{id} for $target->{project}/$target->{package}\n";
    }
    
}

my %list;

%list = ();

for my $request (@{$mywork->{declined}}) {
    explain_request($request->{request}, \%list);
}

if (%list) {
    print "Your declined requests (please revoke or reopen):\n";
    my @lkeys = keys %list;
    foreach my $request (sort { $a <=> $b } @lkeys) {
	print $list{$request};
    }
}

%list = ();

for my $request (@{$mywork->{new}}) {
    # stupid ruby... :)
    explain_request($request->{request}, \%list);
}

if (%list) {
    print "Other new requests:\n";
    my @lkeys = keys %list;
    foreach my $request (sort { $a <=> $b } @lkeys) {
	print $list{$request};
    }
}

