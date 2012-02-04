#! /usr/bin/perl

require LWP::UserAgent;
use JSON;
use POSIX;
use Carp::Always;
use Data::Dumper;
use URI::Escape;

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

my $shortener = LWP::UserAgent->new;

sub shorten_url($$)
{
    my ($url, $slug) = @_;

#    return $url;
    $url = uri_escape($url);
    my $ret = $shortener->get("http://s.kulow.org/-/?url=$url&api=6cf62823d52e6d95582c07f55acdecc7&custom_url=$slug&overwrite=1");
    die $ret->status_line unless ($ret->is_success);
    return $ret->decoded_content;
}

($mywork, $projstat) = fetch_user_infos($user);

#print to_json($mywork, {pretty => 1 });
#print to_json($projstat, {pretty => 1});

my %projects;
for my $package (@{$projstat}) {
    $projects{$package->{develproject}} ||= [];
    push($projects{$package->{develproject}}, $package);
}

my $baseurl = "https://build.opensuse.org/";

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
	if (($review->{by_user} || '') eq $user) {
	    print "Request $request->{id} is waiting for your review!\n";
	    print "  https://build.opensuse.org/request/show/$request->{id}\n\n";
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
	    $reviews_by{"$bproj/$bpack"} = $request;
	    next;
	}
    }
}

for my $project (sort(keys %projects)) {
    my $lines = {};

    for my $package (@{$projects{$project}}) {
	next if @{$package->{requests_from}};

	# do not show version information if there is more important stuff
	my $showversion = 1;
	my $ignorechanges = 0;

	my $key = "$project/$package->{name}";
	if ($reviews_by{$key}) {
	    my $r = $reviews_by{$key};
	    push(@{$lines->{reviews}}, "  $package->{name} has request $r->{id} waiting for review!");
	    delete $reviews_by{$key};
	}

	if ($package->{firstfail} && $package->{develfirstfail}) {
	    my $fail = time_distance($package->{firstfail});
	    my $comment = $package->{failedcomment};
	    $comment =~ s,^\s+,,;
	    $comment =~ s,\s+$,,;
	    my $url = "$baseurl/package/live_build_log?arch=" . uri_escape($package->{failedarch});
	    $url   .= "&package=" . uri_escape($package->{name});
	    $url   .= "&project=" . uri_escape($tproject);
	    $url   .= "&repository=" . uri_escape($package->{failedrepo});
	    $url = shorten_url($url, "bf-$package->{name}");
	    push(@{$lines->{fails}}, "  $package->{name} fails for $fail ($comment):");
	    push(@{$lines->{fails}}, "    $url\n");
	    $ignorechanges = 1;
	}

	for my $problem (sort @{$package->{problems}}) {
	    if ($problem eq 'different_changes') {
		my $url = "$baseurl/package/rdiff?opackage=$package->{name}&oproject=$tproject&package=$package->{develpackage}&project=$package->{develproject}";
		if ($ignorechanges == 0) {
		    $url = shorten_url($url, "rd-$package->{name}");
		    push(@{$lines->{unsubmit}}, "    $package->{name} - $url");
		    $showversion = 0;
		}
	    } elsif ($problem eq 'currently_declined') {
                my $url = "https://build.opensuse.org/request/show/$package->{currently_declined}";
		push(@{$lines->{declined}}, "    $package->{name} - $url");
		$showversion = 0;
	    } 
	}
	
	for my $request (@{$package->{requests_to}}) {
	    push(@{$lines->{requests}}, "    $package->{name} - https://build.opensuse.org/request/show/$request");
	    $requests_to_ignore{$request} = 1;
	    $showversion = 0;
	}

	if ($showversion && $package->{upstream_version}) {
	    push(@{$lines->{upstream}}, "    $package->{name} - packaged: $package->{version}, upstream: $package->{upstream_version}");
	}
    }
    print "Project $project\n" if %$lines;
    for my $reason (qw(reviews fails declined unsubmit requests upstream)) {
	next unless $lines->{$reason};
	if ($reason eq "fails") {
           print "\n";
	} elsif ($reason eq "upstream") {
	   print "\n  Packages with new upstream versions:\n";
	} elsif ($reason eq "unsubmit") {
	   print "\n  Packages with unsubmitted changes:\n";
	} elsif ($reason eq "requests") {
   	   print "\n  Packages with pending requests:\n";
	} elsif ($reason eq "declined") {
	   print "\n  Declined submit requests - please check the reason:\n";
	}

	print join("\n",@{$lines->{$reason}}); 
	print "\n" if ($reason ne "fails");
    }
    print "\n" if %$lines;
}

sub explain_request($$)
{
    my ($request, $list) = @_;
    return if (defined($requests_to_ignore{$request->{id}}));
    #print Dumper($request);
    $actions = $request->{action};
    $actions = [$actions] if (ref($actions) eq "HASH");
    my $line = '';
    for my $action (@{$actions || []}) {
	my $source = $action->{source};
	my $target = $action->{target};
		    
	if ($action->{type} eq "submit") {
	    $line .= "  Submit request from $source->{project}/$source->{package} to $target->{project}\n";
	} elsif ($action->{type} eq "delete") {
	    $line .= "  Delete request for $target->{project}/$target->{package}\n";
	} elsif ($action->{type} eq "maintenance_release") {
	    $line .= "  Maintenance release request from $source->{project}/$source->{package} to $target->{project}\n";
	} elsif ($action->{type} eq "maintenance_incident") {
	    $line .= "  Maintenance incident request from $source->{project}/$source->{package} to $target->{project}\n";
	} elsif ($action->{type} eq "add_role") {
	    $line .= "  User $action->{person}->{name} wants to be $action->{person}->{role} in $target->{project}\n";
	} elsif ($action->{type} eq "change_devel") {
            $line .= "  Package $target->{project}/$target->{package} should be developed in $source->{project}\n";
	} else {
	    print STDERR "HUH" . Dumper($action);
	}
    }
    $list->{int($request->{id})} = $line if ($line);
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
	print "    https://build.opensuse.org/request/show/$request\n\n";
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
	print "    https://build.opensuse.org/request/show/$request\n\n";
    }
}

