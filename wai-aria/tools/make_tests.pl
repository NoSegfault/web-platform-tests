#!/usr/bin/perl
#
#  make_tests.pl - generate WPT test cases from the testable statements wiki
#
#  This script assumes that a wiki has testable statement entries
#  in the format described by the specification at
#  https://spec-ops.github.io/atta-api/index.html
#
#  usage: make_tests.pl -f file | -w wiki_title | -s spec -d dir

use strict;

use IO::String ;
use JSON ;
use MediaWiki::API ;
use Getopt::Long;

my %specs = (
    "aria11" => {
      title => "ARIA_1.1_Testable_Statements",
      specURL => "https://www.w3.org/TR/wai-aria11/"
    },
    "svg" => {
      title => "SVG_Accessibility/Testing/Test_Assertions_with_Tables",
      specURL => "https://www.w3.org/TR/svg-aam-1.0/"
    }
);

my @apiNames = qw(UIA MSAA ATK IAccessible2 AXAPI);

# the suffix to attach to the automatically generated test case names
my $theSuffix = "-manual.html";

# dir is determined based upon the short name of the spec and is defined
# by the input or on the command line

my $file = undef ;
my $spec = undef ;
my $wiki_title = undef ;
my $dir = undef;

my $result = GetOptions(
    "f|file=s"   => \$file,
    "w|wiki=s"   => \$wiki_title,
    "s|spec=s"   => \$spec,
    "d|dir=s"   => \$dir);

my $wiki_config = {
  "api_url" => "https://www.w3.org/wiki/api.php"
};

if (!$dir) {
  $dir = "../raw";
}

if (!-d $dir) {
  print STDERR "No such directory: $dir\n";
  exit 1;
}

my $io ;
our $theSpecURL = "";

if ($spec) {
  $wiki_title = $specs{$spec}->{title};
  $theSpecURL = $specs{$spec}->{specURL};
}

if ($wiki_title) {
  my $MW = MediaWiki::API->new( $wiki_config );
  my $page = $MW->get_page( { title => $wiki_title } );
  my $theContent = $page->{'*'};
  $io = IO::String->new($theContent);
} elsif ($file) {
  open($io, "<", $file) || die("Failed to open $file: " . $@);
} else {
  usage() ;
}

# Now let's walk through the content and build a test page for every item
#

# iterate over the content

# my $io ;
# open($io, "<", "raw") ;

my $state = 0;   # between items
my $current = "";
my $theCode = "";
my $theAttributes = {};
my $theAsserts = {} ;
my $theAssertCount = 0;
my $theAPI = "";
my $typeRows = 0;
my $theType = "";
my $theName = "";
my $theRef = "";

while (<$io>) {
  # look for state
  if (m/^SpecURL: (.*)$/) {
    $theSpecURL = $1;
    $theSpecURL =~ s/^ *//;
    $theSpecURL =~ s/ *$//;
  }
  if (m/^=== (.*) ===/) {
    if ($state != 0) {
      # we were in an item; dump it
      build_test($current, $theAttributes, $theCode, $theAsserts) ;
      print "Finished $current\n";
    }
    $state = 1;
    $current = $1;
    $theAttributes = {} ;
    $theCode = "";
    $theAsserts = {};
    $theName = "";
  }
  if ($state == 1) {
    if (m/<pre>/) {
      # we are now in the code block
      $state = 2;
      next;
    } elsif (m/==== +(.*) +====/) {
      # we are in some other block
      $theName = lc($1);
      $theAttributes->{$theName} = "";
      next;
    }
    if (m/^Reference: +(.*)$/) {
      $theAttributes->{reference} = $theSpecURL . "#" . $1;
    } elsif ($theName ne "") {
      # accumulate whatever was in the block under the data for it
      chomp();
      $theAttributes->{$theName} .= $_;
    }
  }
  if ($state == 2) {
    if (m/<\/pre>/) {
      # we are done with the code block
      $state = 3;
    } else  {
      if (m/^\s/ && !m/if given/) {
        $theCode .= $_;
      }
    }
  } elsif ($state == 3) {
    # look for a table
    if (m/^\{\|/) {
      # table started
      $state = 4;
    }
  } elsif ($state == 4) {
    if (m/^\|-/) {
      if ($theAPI
          && exists($theAsserts->{$theAPI}->[$theAssertCount])
          && scalar(@{$theAsserts->{$theAPI}->[$theAssertCount]})) {
        $theAssertCount++;
      }
      # start of a table row
      if ($theType ne "" && $typeRows) {
        print qq($theType typeRows was $typeRows\n);
        # we are still processing items for a type
        $typeRows--;
        # populate the first cell
        $theAsserts->{$theAPI}->[$theAssertCount] = [ $theType ] ;
      } else {
        $theType = "";
      }
    } elsif (m/^\|\}/) {
      # ran out of table
      $state = 5;
    } elsif (m/^\|rowspan="*([0-9])"*\|(.*)$/) {
      my $rows = $1;
      my $theString = $2;
      $theString =~ s/ +$//;
      $theString =~ s/^ +//;
      if (grep { $_ eq $theString } @apiNames) {
        $theAssertCount = 0;
        # this is a new API section
        $theAPI = $theString ;
        $theAsserts->{$theAPI} = [ [] ] ;
        $theType = "";
      } else {
        # this is a multi-row type
        $theType = $theString;
        $typeRows = $rows;
        print qq(Found multi-row $theString for $theAPI with $typeRows rows\n);
        $typeRows--;
        # populate the first cell
        if ($theAPI
            && exists($theAsserts->{$theAPI}->[$theAssertCount])
            && scalar(@{$theAsserts->{$theAPI}->[$theAssertCount]})) {
          $theAssertCount++;
        }
        $theAsserts->{$theAPI}->[$theAssertCount] = [ $theType ] ;
      }
    } elsif (m/^\|(.*)$/) {
      my $item = $1;
      $item =~ s/^ *//;
      $item =~ s/ *$//;
      $item =~ s/^['"]//;
      $item =~ s/['"]$//;
      # add into the data structure for the API
      if (!exists $theAsserts->{$theAPI}->[$theAssertCount]) {
        $theAsserts->{$theAPI}->[$theAssertCount] = [ $item ] ;
      } else {
        push(@{$theAsserts->{$theAPI}->[$theAssertCount]}, $item);
      }
    }
  }
};

if ($state != 0) {
  build_test($current, $theAttributes, $theCode, $theAsserts) ;
  print "Finished $current\n";
}

exit 0;


sub build_test() {
  my $title = shift ;
  my $attrs = shift ;
  my $code = shift ;
  my $asserts = shift;

  if ($title eq "") {
    print "No name provided!";
    return;
  }

  my $title_reference = $title;

  if ($code eq "") {
    print "No code for $title; skipping.\n";
    return;
  }

  if ( $asserts eq {}) {
    print "No code or assertions for $title; skipping.\n";
    return;
  }

  $asserts->{WAIFAKE} = [ [ "property", "role", "is", "ROLE_TABLE_CELL" ], [ "property", "interfaces", "contains", "TableCell" ] ];

  # massage the data to make it more sensible
  if (exists $asserts->{"ATK"}) {
    print "processing ATK for $title\n";
    my @conditions = @{$asserts->{"ATK"}};
    for (my $i = 0; $i < scalar(@conditions); $i++) {
      my @new = ();
      my $start = 0;
      my $assert = "true";
      if ($conditions[$i]->[0] =~ m/^NOT/) {
        $start = 1;
        $assert = "false";
      }

      print qq(Looking at $title $conditions[$i]->[$start]\n);
      if ($conditions[$i]->[$start] =~ m/^ROLE_/) {
        $new[0] = "role";
        $new[1] = $conditions[$i]->[$start];
        $new[2] = $assert;
      } elsif ($conditions[$i]->[$start] =~ m/(.*) interface/i) {
        $new[0] = "interface";
        $new[1] = $1;
        print "$1 condition is " . $conditions[$i]->[1] . "\n";
        if ($conditions[$i]->[1] ne '<shown>'
          && $conditions[$i]->[1] !~ m/true/i ) {
          $assert = "false";
        }
        $new[2] = $assert;
      } elsif ($conditions[$i]->[$start] eq "object" || $conditions[$i]->[$start] eq "attribute" ) {
        $new[0] = "attribute";
        my $val = $conditions[$i]->[2];
        $val =~ s/"//g;
        $new[1] = $conditions[$i]->[1] . ":" . $val;
        if ($conditions[$i]->[3] eq "not exposed"
            || $conditions[$i]->[3] eq "false") {
          $new[2] = "false";
        } else {
          $new[2] = "true";
        }
      } elsif ($conditions[$i]->[$start] =~ m/^STATE_/) {
        $new[0] = "state";
        $new[1] = $conditions[$i]->[$start];
        $new[2] = $assert;
      } elsif ($conditions[$i]->[$start] =~ m/^object attribute (.*)/) {
        my $name = $1;
        $new[0] = "attribute";
        my $val = $conditions[$i]->[1];
        $val =~ s/"//g;
        if ($val eq "not exposed" || $val eq "not mapped") {
          $new[1] = $name;
          $new[2] = "false";
        } else {
          $new[1] = $name . ":" . $val;
          $new[2] = "true";
        }
      } else {
        @new = @{$conditions[$i]};
        if ($conditions[$i]->[2] eq '<shown>') {
          $new[2] = "true";
        }
      }
      $conditions[$i] = \@new;
    }
    $asserts->{"ATK"} = \@conditions;
  }


  my $testDef =
    { "title" => $title,
      "steps" => [
        {
          "type"=>  "test",
          "title"=> "step 1",
          "element"=> "test",
          "test" => $asserts
        }
    ]
  };

  if (scalar(keys(%$attrs))) {
    while (my $key = each(%$attrs)) {
      print "Copying $key \n";
      $testDef->{$key} = $attrs->{$key};
    }
  }

  if (exists $attrs->{reference}) {
    $title_reference = "<a href='" . $attrs->{reference} . "'>" . $title_reference . "</a>" ;
  }

  my $testDef_json = to_json($testDef, { pretty => 1, utf8 => 1});

  my $fileName = $title;
  $fileName =~ s/\s*$//;
  $fileName =~ s/"//g;
  $fileName =~ s/\///g;
  $fileName =~ s/\s+/_/g;
  $fileName =~ s/=/_/g;
  $fileName .= $theSuffix;

  my $template = qq(<!doctype html>
<html>
<head>
<title>$title</title>
<link rel="stylesheet" href="/resources/testharness.css">
<link rel="stylesheet" href="/wai-aria/scripts/manual.css">
<script src="/resources/testharness.js"></script>
<script src="/resources/testharnessreport.js"></script>
<script src="/wai-aria/scripts/ATTAcomm.js"></script>
<script>
setup({explicit_timeout: true, explicit_done: true });

var theTest = new ATTAcomm(
$testDef_json
) ;
</script>
</head>
<body>
<p>This test examines the ARIA properties for $title_reference.</p>
$code
<div id="manualMode"></div>
<div id="log"></div>
<div id="ATTAmessages"></div>
</body>
</html>
);

  my $file ;

  if (open($file, ">", "$dir/$fileName")) {
    print $file $template;
    close $file;
  } else {
    print qq(Failed to create file "$dir/$fileName" $!\n);
  }

  return;
}

sub usage() {
  print STDERR q(usage: make_tests.pl -f file | -w wiki_title | -s spec [-n -v -d dir ]

  -s specname   - the name of a spec known to the system
  -w wiki_title - the TITLE of a wiki page with testable statements
  -f file       - the file from which to read

  -n            - do nothing
  -v            - be verbose
  -d dir        - put generated tests in directory dir
  );
  exit 1;
}

# vim: ts=2 sw=2 ai:
