#!/usr/bin/php
<?php

const REGEX_LINE = '/\S+ \S+ \S+ \[([^:]+:\d+:\d+:\d+ [^\]]+)\] "(\S+)(?: (\S+) \S+)?" (\S+) (\S+) "[^"]*" "[^"]*" .* size:(\S+) \S+(?: +"?(\S+-\S+-\S+-\S+-[^\s"]+|-)"? "?(dvd|ftp|-)"?)?/';
const REGEX_PRODUCT = '#/(?:(tumbleweed)|distribution/(?:leap/)?(\d+\.\d+)|openSUSE(?:_|:/)(?:leap(?:_|:/))?(factory|tumbleweed|\d+\.\d+))#i';
const REGEX_IMAGE = '#(?:/(?:iso|live)/[^/]+-(DVD|NET|GNOME-Live|KDE-Live|Rescue-CD|Kubic-DVD)-[^/]+\.iso(?:\.torrent)?|/jeos/[^/]+-(JeOS)\.[^/]+\.(?:qcow2|vhdx|vmdk|vmx)$)#';

$total = 0;
$total_invalid = 0;
$total_product = [];
$unique_product = [];
$total_image_product = [];

$file = $argc == 2 ? $argv[1] : 'php://stdin';
$handle = fopen($file, 'r');
while (($line = fgets($handle)) !== false) {
  if (!preg_match(REGEX_LINE, $line, $match)) {
    error_log('[failed to parse] ' . rtrim($line));
    $total_invalid++;
    continue;
  }

  // Only interested in GET or HEAD requests, others are invalid.
  if ($match[2] != 'GET' && $match[2] != 'HEAD') continue;
  $total++;

  // Attempt to determine for which product was the request.
  if (!preg_match(REGEX_PRODUCT, $match[3], $match_product)) {
    continue;
  }

  // Remove empty match groups and select non-all match.
  $values = array_filter($match_product);
  $product = str_replace('factory', 'tumbleweed', strtolower(next($values)));

  if (!isset($total_product[$product])) $total_product[$product] = 0;
  $total_product[$product] += 1;

  if (count($match) == 9 && $match[7] != '-') {
    $uuid = $match[7];
    if (!isset($unique_product[$product])) $unique_product[$product] = [];
    if (!isset($unique_product[$product][$uuid])) $unique_product[$product][$uuid] = 0;
    $unique_product[$product][$uuid] += 1;
  }

  if (preg_match(REGEX_IMAGE, $match[3], $match_image)) {
    // Remove empty match groups and select non-all match.
    $values = array_filter($match_image);
    $image = next($values);
    if (!isset($total_image_product[$product])) $total_image_product[$product] = [];
    if (!isset($total_image_product[$product][$image])) $total_image_product[$product][$image] = 0;
    $total_image_product[$product][$image] += 1;
  }
}
$position = ftell($handle);
fclose($handle);

error_log('processed ' . number_format($position) . ' bytes');
error_log('found ' . number_format($total) . ' requests across ' .
  number_format(count($total_product)) . ' products');

ksort($total_product);
ksort($unique_product);
echo json_encode([
  'total' => $total,
  'total_product' => $total_product,
  'unique_product' => $unique_product,
  'total_image_product' => $total_image_product,
  'total_invalid' => $total_invalid,
  'bytes' => $position,
]) . "\n"; // JSON_PRETTY_PRINT for debugging.
