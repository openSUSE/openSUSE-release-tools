#! /usr/bin/perl

require LWP::UserAgent;
use JSON;

my $user = $ARGV[0];
my $project = "openSUSE:Factory";

sub fetch_user_infos($)
{
    my ($user) = @_;

    if (-f "reports/$user") {
	open( my $fh, '<', "reports/$user" );
	my $json_text   = <$fh>;
	my $st = decode_json( $json_text );
	close($fh);
	return $st->{mywork}, $st->{projstat};
    }

    my $ua = LWP::UserAgent->new;
    $ua->timeout(15);
    $ua->default_header("Accept" => "application/json");
    $mywork = $ua->get("https://build.opensuse.org/stage/home/my_work?user=$user");
    unless ($mywork->is_success) { die $mywork->status_line; }

    $mywork = from_json( $mywork->decoded_content, { utf8 => 1 });

    my $url = "https://build.opensuse.org/stage/project/status?project=$project&ignore_pending=0";
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
    return $mywork, $projstat;
}

$mywork, $projstat = fetch_user_infos($user);

#print to_json($mywork, {pretty => 1 });
#print to_json($projstat, {pretty => 1});

