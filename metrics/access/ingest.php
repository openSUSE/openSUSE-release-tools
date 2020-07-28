#!/usr/bin/php
<?php

include 'utils.php';

const REGEX_LINE = '/\S+ \S+ \S+ \[([^:]+:\d+:\d+:\d+ [^\]]+)\] "(\S+)(?: (\S+) \S+)?" (\S+) (\S+) "[^"]*" "[^"]*" .* size:(\S+) \S+(?: +"?(\S+-\S+-\S+-\S+-[^\s"]+|-)"? "?(dvd|ftp|-)"?)?/';
const REGEX_PRODUCT = '#/(?:(tumbleweed)|distribution/(?:leap/)?(\d+\.\d+)|openSUSE(?:_|:/)(?:leap(?:_|:/))?(factory|tumbleweed|\d+\.\d+))#i';
const REGEX_IMAGE = '#(?:/(?:iso|live)/[^/]+-(DVD|NET|GNOME-Live|KDE-Live|Rescue-CD|Kubic-DVD)-[^/]+\.iso(?:\.torrent)?|/jeos/[^/]+-(JeOS)\.[^/]+\.(?:qcow2|vhdx|vmdk|vmx)$)#';
const REGEX_RPM_NAME = '#(?:^/.+/([\w+-\.]+)\.rpm$)#i';

$total = 0;
$total_invalid = 0;
$total_product = [];
$unique_product = [];
$total_image_product = [];
$total_package_product = [];
$fallback_packages = get_packages_list('tumbleweed');

function exception_error_handler($severity, $message, $file, $line) {
  if (!(error_reporting() & $severity)) {
    // This error code is not included in error_reporting
    return;
  }
  throw new ErrorException($message, 0, $severity, $file, $line);
}
set_error_handler("exception_error_handler");

function get_packages_list($product) {
  $packages_file = __DIR__ . "packages/" . $product;
  try {
    $packages = file($packages_file, FILE_IGNORE_NEW_LINES);
  } catch (ErrorException $e) {
    echo 'Has not found packages file for ', $product, ". Using fallback.\n";
    return null;
  }
  $packages = array_map('trim', $packages);
  sort($packages);
  return $packages;
}

// Find a substring at the beginning of a string from an array of substrings
// $substrings - array of possible substrings (needles)
// $string - examined string (haystack)
// Returns the first match
function find_substring($substrings, $string) {
  $result_index = binary_string_search($substrings, 0, count($substrings) - 1, $string);
  if ($result_index >= 0)
    return check_next_element($substrings, $string, $result_index, $substrings[$result_index]);
  else
    return NULL;
}

function check_next_element($substrings, $string, $index, $match) {
  if (stripos($string, $substrings[$index + 1]) === 0)
    return check_next_element($substrings, $string, $index + 1, $substrings[$index + 1]);

  elseif (stripos($substrings[$index + 1], $match) === 0 &&
    strncmp($substrings[$index + 1], $string, strlen($string)) < 0)
    return check_next_element($substrings, $string, $index + 1, $match);

  else
    return $match;
}

function binary_string_search($haystack, $start, $end, $needle) {
  if ($end < $start)
    return false;

  $mid_index = floor(($end + $start)/2);
  $comparison = strncmp($haystack[$mid_index], $needle, strlen($haystack[$mid_index]));
  if ($comparison == 0)
    return $mid_index;

  elseif ($comparison > 0)
    return binary_string_search($haystack, $start, $mid_index - 1, $needle);

  else
    return binary_string_search($haystack, $mid_index + 1, $end, $needle);
}

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
  // Not interested on errors.
  if ($match[4] >= '400') continue;
  $total++;

  // Attempt to determine for which product was the request.
  if (!preg_match(REGEX_PRODUCT, $match[3], $match_product)) {
    continue;
  }

  // Remove empty match groups and select non-all match.
  $values = array_filter($match_product);
  $product = str_replace('factory', 'tumbleweed', strtolower(next($values)));

  if (!isset($total_product[$product])) {
    $total_product[$product] = 0;
    if (product_filter($product)) {
      $packages[$product] = get_packages_list($product);
      if (is_null($packages[$product])) {
        $packages[$product] = &$fallback_packages;
      }
    }
  }
  $total_product[$product] += 1;

  if (product_filter($product) && preg_match(REGEX_RPM_NAME, $match[3], $match_rpm_name)) {
    $package = find_substring($packages[$product], $match_rpm_name[1]);
    if ($package) {
      if (!isset($total_package_product[$product])) $total_package_product[$product] = [];
      if (!isset($total_package_product[$product][$package])) $total_package_product[$product][$package] = 0;
      $total_package_product[$product][$package] += 1;
    }
  }

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
  'total_package_product' => $total_package_product,
  'total_invalid' => $total_invalid,
  'bytes' => $position,
]) . "\n"; // JSON_PRETTY_PRINT for debugging.
