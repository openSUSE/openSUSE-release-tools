#!/usr/bin/php
<?php

const REGEX_LINE = '/(\S+) \S+ \S+ \[([^:]+:\d+:\d+:\d+ [^\]]+)\] "(\S+)(?: (\S+) \S+)?" (\S+) (\S+) "[^"]*" "[^"]*" .* (?:size:|want:- give:- \d+ )(\S+) \S+(?: +"?(\S+-\S+-\S+-\S+-[^\s"]+|-)"? "?(dvd|ftp|mini|usb-[^"]*|livecd-[^"]*|appliance-?[^"]*|-)"?)?/';
const REGEX_PRODUCT = '#/(?:(tumbleweed)|distribution/(?:leap/)?(\d+\.\d+)|openSUSE(?:_|:/)(?:leap(?:_|:/))?(factory|tumbleweed|\d+\.\d+))#i';
const REGEX_IMAGE = '#(?:/(?:iso|live)/[^/]+-(DVD|NET|GNOME-Live|KDE-Live|Rescue-CD|Kubic-DVD)-[^/]+\.iso(?:\.torrent)?|/jeos/[^/]+-(JeOS)\.[^/]+\.(?:qcow2|vhdx|vmdk|vmx)$)#';
const REGEX_IPV4 = '/^((25[0-5]|(2[0-4]|1\d|[1-9]|)\d)\.?\b){4}$/';
const PROTOCOLS = ['ipv4', 'ipv6'];

$total_invalid = 0;
foreach (PROTOCOLS as $protocol) {
  $total[$protocol] = 0;
  $total_product[$protocol] = [];
  $unique_product[$protocol] = [];
  $total_image_product[$protocol] = [];
}

$file = $argc == 2 ? $argv[1] : 'php://stdin';
$handle = fopen($file, 'r');
while (($line = fgets($handle)) !== false) {
  $protocol = '';
  if (!preg_match(REGEX_LINE, $line, $match)) {
    error_log('[failed to parse] ' . rtrim($line));
    $total_invalid++;
    continue;
  }

  // Only interested in GET or HEAD requests, others are invalid.
  if ($match[3] != 'GET' && $match[3] != 'HEAD') continue;
  // Not interested on errors.
  if ($match[5] >= '400') continue;

  if (preg_match(REGEX_IPV4, $match[1]))
    $protocol = 'ipv4';
  else
    $protocol = 'ipv6';
  $total[$protocol]++;

  // Attempt to determine for which product was the request.
  if (!preg_match(REGEX_PRODUCT, $match[4], $match_product)) {
    continue;
  }

  // Remove empty match groups and select non-all match.
  $values = array_filter($match_product);
  $product = str_replace('factory', 'tumbleweed', strtolower(next($values)));

  if (!isset($total_product[$protocol][$product])) $total_product[$protocol][$product] = 0;
  $total_product[$protocol][$product] += 1;

  if (count($match) == 10 && $match[8] != '-') {
    $uuid = $match[8];
    if (!isset($unique_product[$protocol][$product])) $unique_product[$protocol][$product] = [];
    if (!isset($unique_product[$protocol][$product][$uuid])) {
      $unique_product[$protocol][$product][$uuid] = [
        'count' => 0,
        'flavor' => $match[9],
        'ip' => $match[1],
      ];
    }
    $unique_product[$protocol][$product][$uuid]['count'] += 1;
  }

  if (preg_match(REGEX_IMAGE, $match[4], $match_image)) {
    // Remove empty match groups and select non-all match.
    $values = array_filter($match_image);
    $image = next($values);
    if (!isset($total_image_product[$protocol][$product])) $total_image_product[$protocol][$product] = [];
    if (!isset($total_image_product[$protocol][$product][$image])) $total_image_product[$protocol][$product][$image] = 0;
    $total_image_product[$protocol][$product][$image] += 1;
  }
}
$position = ftell($handle);
fclose($handle);

error_log('processed ' . number_format($position) . ' bytes');
error_log('found ' . number_format(array_sum($total)) . ' requests across ' .
  number_format(array_sum(array_map('count', $total_product))) . ' products');

$output = [
  'total_invalid' => $total_invalid,
  'bytes' => $position
];
foreach (PROTOCOLS as $protocol) {
  ksort($total_product[$protocol]);
  ksort($unique_product[$protocol]);
  $output[$protocol] = [
    'total' => $total[$protocol],
    'total_product' => $total_product[$protocol],
    'unique_product' => $unique_product[$protocol],
    'total_image_product' => $total_image_product[$protocol]
  ];
}

if ($position) {
  echo json_encode($output) . "\n"; // JSON_PRETTY_PRINT for debugging.
}
