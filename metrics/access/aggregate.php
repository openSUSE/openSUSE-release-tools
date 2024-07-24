#!/usr/bin/php
<?php

use InfluxDB2\Client;
use InfluxDB2\Point;

$CACHE_DIR = $_SERVER['HOME'] . '/.cache/openSUSE-release-tools/metrics-access';
const PROTOCOLS = ['ipv4', 'ipv6'];
const DOWNLOAD_OPENSUSE_ORG = 'https://download.opensuse.org/logs';
const PONTIFEX = 'http://pontifex.infra.opensuse.org/logs';
const BACKUP = 'http://backup.infra.opensuse.org';
const LANGLEY = 'http://langley.suse.de/pub/pontifex%s-opensuse.suse.de';
const VHOST = 'download.opensuse.org';
const FILENAME = 'download.opensuse.org-%s-access_log.xz';
const IPV6_PREFIX = 'ipv6.';
const PRODUCT_PATTERN = '/^(10\.[2-3]|11\.[0-4]|12\.[1-3]|13\.[1-2]|42\.[1-3]|15\.[0-6]|tumbleweed|slowroll)$/';

$begin = new DateTime();
// Skip the current day since the logs are incomplete and not compressed yet.
$begin->sub(date_interval_create_from_date_string('1 day'));
$source_map = [
  'ipv4' => [
    // the first item defines the starting date for aggregation
    '2023-01-01' => false,
    '2023-11-13' => DOWNLOAD_OPENSUSE_ORG . '/' . VHOST,
    'filename' => FILENAME,
  ],
  'ipv6' => [
    '2012-12-31' => false,
    '2023-11-13' => DOWNLOAD_OPENSUSE_ORG . '/' . IPV6_PREFIX . VHOST,
    'filename' => IPV6_PREFIX . FILENAME,
  ],
  'ipv4+6' => [
    '2023-11-13' => false,
    $begin->format('Y-m-d') => DOWNLOAD_OPENSUSE_ORG . '/' . VHOST,
    'filename' => FILENAME,
  ],
];
$end = new DateTime(key($source_map['ipv4'])); // decide about adding one day
$migration_date = new DateTime(key($source_map['ipv4+6']));
$period_reversed = date_period_reversed($end, '1 day', $begin);

error_log('begin: ' . $begin->format('Y-m-d'));
error_log('end:   ' . $end->format('Y-m-d'));
error_log('count: ' . number_format(count($period_reversed)) . ' days');

cache_init();
ingest_all($period_reversed, $source_map);
aggregate_all(array_reverse($period_reversed));


function cache_init()
{
  global $CACHE_DIR;
  if (!file_exists($CACHE_DIR)) {
    foreach (PROTOCOLS as $protocol) {
      mkdir("$CACHE_DIR/$protocol", 0755, true);
    }
    mkdir("$CACHE_DIR/ipv4+6", 0755, true);

    // Avoid packaging mess while still automating, but not ideal.
    passthru('cd ' . escapeshellarg($CACHE_DIR) .
      ' && composer require influxdata/influxdb-client-php:~3.4 guzzlehttp/guzzle');
  }

  require "$CACHE_DIR/vendor/autoload.php";
}

function ingest_all($period_reversed, $source_map)
{
  global $CACHE_DIR;
  $source = [];
  $found = [];
  // Walk backwards until found in cache.
  foreach ($period_reversed as $date) {
    $date_string = print_date($date);
    $protocols_on_day = get_protocols($date);

    foreach ($protocols_on_day as $protocol) {
      if (!empty($found[$protocol])) continue;
      if (isset($source_map[$protocol][$date_string]))
        $source[$protocol] = $source_map[$protocol][$date_string];

      // Skip date+protocol if no source is available.
      if (empty($source[$protocol])) continue;

      $cache_file = get_cache_file($protocol, $date);
      if (file_exists($cache_file)) {
        error_log("[$date_string] [$protocol] found");
        $found[$protocol] = true;
      } else {
        error_log("[$date_string] [$protocol] ingest");
        ingest($date, $source[$protocol], $source_map[$protocol]['filename'], $cache_file);
      }
    }

    // Stop when all cache files were found
    if (count($found) == count($protocols_on_day)) {
      error_log('ingest initialization complete');
      break;
    }
  }

  // Wait for all ingest processes to complete before proceeding.
  subprocess_wait(1, 1);
}

function print_date($date)
{
  return $date->format('Y-m-d');
}

// Logs before migration date have been kept in separate files for IPv4 and IPv6 addresses
function has_separate_protocol_logs($date)
{
  global $migration_date;
  if ($date > $migration_date)
    return false;
  else
    return true;
}

function get_cache_file($protocol, $date)
{
  global $CACHE_DIR;
  if (has_separate_protocol_logs($date))
    return "$CACHE_DIR/$protocol/" . print_date($date) . ".json";
  else
    return "$CACHE_DIR/ipv4+6/" . print_date($date) . ".json";
}

function get_cache_files($date)
{
  $files = [];
  foreach (get_protocols($date) as $protocol)
    array_push($files, get_cache_file($protocol, $date));

  return $files;
}

function get_protocols($date)
{
  if (has_separate_protocol_logs($date))
    return PROTOCOLS;
  else
    return array("ipv4+6");
}

function ingest($date, $source, $filename, $destination)
{
  $url = implode('/', [
    $source,
    $date->format('Y'),
    $date->format('m'),
    sprintf($filename, $date->format('Ymd')),
  ]);
  $command = implode(' ', [
    'curl -s --digest --netrc',
    escapeshellarg($url),
    '| xzcat',
    '| ' . __DIR__ . '/ingest.php',
    '> ' . escapeshellarg($destination),
    '&',
  ]);
  error_log($command);
  passthru_block($command);
}

function passthru_block($command)
{
  static $cpu_count = null;

  if (!$cpu_count) {
    $cpuinfo = file_get_contents('/proc/cpuinfo');
    preg_match_all('/^processor/m', $cpuinfo, $matches);
    $cpu_count = max(count($matches[0]), 1);
    error_log("detected $cpu_count cores");
  }

  $group_size = substr_count($command, '|') + 1;
  subprocess_wait($group_size, $cpu_count);

  passthru($command, $exit_code);
  if ($exit_code != 0) {
    error_log('failed to start process');
    exit(1);
  }
}

function subprocess_wait($group_size, $cap)
{
  while (subprocess_count() / $group_size >= $cap) {
    usleep(250000);
  }
}

function subprocess_count()
{
  return substr_count(shell_exec('pgrep -g ' . getmypid()), "\n") - 1;
}

function aggregate_all($period)
{
  global $CACHE_DIR;
  $intervals = ['day' => 'Y-m-d', 'week' => 'Y-W', 'month' => 'Y-m', 'FQ' => null];
  $merged = [];
  $merged_protocol = [];
  $date_previous = null;
  foreach ($period as $date) {
    $date_string = print_date($date);

    $data = null;
    foreach (PROTOCOLS as $protocol) {
      $cache_file = get_cache_file($protocol, $date);
      if (!file_exists($cache_file) or !filesize($cache_file)) continue;

      error_log("[$date_string]" . (has_separate_protocol_logs($date) ? " [$protocol]" : "") . " load cache");
      $data_new = json_decode(file_get_contents($cache_file), true);
      if (!$data_new) {
        error_log('ERROR: failed to load ' . $cache_file);
        unlink($cache_file); // Trigger it to be re-ingested next run.
        exit(1);
      }

      if (isset($data_new[$protocol])) {
        // new cache files have 'ipv4' and 'ipv6' array keys
        $data_protocol = $data_new[$protocol];
        // we don't want to count 'total_invalid' and 'bytes' twice
        if ($data) {
          $data_protocol['total_invalid'] = 0;
          $data_protocol['bytes'] = 0;
        } else {
          $data_protocol['total_invalid'] = $data_new['total_invalid'];
          $data_protocol['bytes'] = $data_new['bytes'];
        }
      }
      else
        $data_protocol = $data_new;
      if (!isset($merged_protocol[$protocol])) $merged_protocol[$protocol] = [];
      $data_protocol['days'] = 1;
      normalize($data_protocol);
      aggregate($intervals, $merged_protocol[$protocol], $date, $date_previous, $data_protocol,
        ['protocol' => $protocol], 'protocol');

      if ($data) {
        merge($data, $data_protocol);
        $data['days'] = 1;
      } else {
        $data = $data_protocol;
      }
    }

    if (!$data) {
      error_log("[$date_string] skipping due to lack of data");
      continue;
    }

    aggregate($intervals, $merged, $date, $date_previous, $data);

    $date_previous = $date;
  }

  // Write out any remaining data by simulating a date beyond all intervals.
  /*error_log('write remaining data');
  $date = clone $date;
  $date->add(date_interval_create_from_date_string('1 year'));

  foreach (PROTOCOLS as $protocol) {
    aggregate($intervals, $merged_protocol[$protocol], $date, $date_previous, null,
      ['protocol' => $protocol], 'protocol');
  }
  aggregate($intervals, $merged, $date, $date_previous, null);*/
}

function aggregate($intervals, &$merged, $date, $date_previous, $data, $tags = [], $prefix = 'access')
{
  foreach ($intervals as $interval => $format) {
    if ($interval === 'FQ') {
      $value = format_FQ($date);
      if (isset($date_previous))
        $value_previous = format_FQ($date_previous);
    }
    elseif ($interval === 'FY') {
      $value = format_FY($date);
      if (isset($date_previous))
        $value_previous = format_FY($date_previous);
    }
    else {
      $value = $date->format($format);
      if (isset($date_previous))
        $value_previous = $date_previous->format($format);
    }
    if (!isset($merged[$interval]) || $value != $merged[$interval]['value']) {
      if (!empty($merged[$interval]['data'])) {
        $summary = summarize($merged[$interval]['data']);
        if ($prefix === 'protocol') {
          $summary = ['-' => $summary['-']];
        }
        $flavors = [];
        foreach ($summary as $product => $details) {
          if (isset($details['flavors'])) {
            $flavors[$product] = $details['flavors'];
            unset($summary[$product]['flavors']);
          }
        }

        if (isset($value_previous) and $value != $value_previous) {
          $count = write_summary($interval, $date_previous, $summary, $tags, $prefix);
          if (isset($flavors)) {
            $count += write_flavors($interval, $date_previous, $flavors);
          }

          if ($prefix === 'access') {
            $summary = summarize_product_plus_key($merged[$interval]['data']['total_image_product']);
            $count += write_summary_product_plus_key($interval, $date_previous, $summary, 'image');
          }

          error_log("[$prefix] [$interval] [{$merged[$interval]['value']}] wrote $count points at " .
            $date_previous->format('Y-m-d') . " spanning " . $merged[$interval]['data']['days'] . ' day(s)');
        }
      }

      // Reset merge data to current data.
      $merged[$interval] = [
        'data' => $data,
        'value' => $value,
      ];
    }
    // Merge day onto existing data for interval. A more complex approach of
    // merging higher order intervals is overly complex due to weeks.
    else
      merge($merged[$interval]['data'], $data);
  }
}

function format_FQ($date)
{
  $financial_date = clone $date;
  date_add($financial_date, date_interval_create_from_date_string('2 months'));
  $quarter = ceil($financial_date->format('n')/3);

  return $financial_date->format('Y') . '-' . $quarter;
}

function format_FY($date)
{
  $financial_date = clone $date;
  date_add($financial_date, date_interval_create_from_date_string('2 months'));

  return $financial_date->format('Y');
}

function normalize(&$data)
{
  // Ensure fields added later, that are not present in all data, are available.
  if (!isset($data['total_image_product'])) {
    $data['total_image_product'] = [];
  }
  $first_product = reset($data['unique_product']);
  $first_key = reset($first_product);
  if (is_int($first_key)) {
    foreach ($data['unique_product'] as $product => $pairs) {
      foreach ($pairs as $key => $count) {
        $data['unique_product'][$product][$key] = ['count' => $count];
      }
    }
  }
}

function merge(&$data1, $data2)
{
  $data1['days'] += $data2['days'];
  $data1['total'] += $data2['total'];
  foreach ($data2['total_product'] as $product => $total) {
    if (empty($data1['total_product'][$product]))
      $data1['total_product'][$product] = 0;

    $data1['total_product'][$product] += $total;
  }

  merge_unique_products($data1['unique_product'], $data2['unique_product']);
  merge_product_plus_key($data1['total_image_product'], $data2['total_image_product']);

  $data1['total_invalid'] += $data2['total_invalid'];
  $data1['bytes'] += $data2['bytes'];
}

function merge_product_plus_key(&$data1, $data2)
{
  foreach ($data2 as $product => $pairs) {
    if (empty($data1[$product]))
      $data1[$product] = [];

    foreach ($pairs as $key => $value) {
      if (empty($data1[$product][$key]))
        $data1[$product][$key] = 0;

      $data1[$product][$key] += $data2[$product][$key];
    }
  }
}

function merge_unique_products(&$data1, $data2)
{
  foreach ($data2 as $product => $arrays) {
    if (empty($data1[$product]))
      $data1[$product] = [];

    foreach ($arrays as $key => $array) {
      if (empty($data1[$product][$key]))
        $data1[$product][$key] = ['count' => 0];

      $data1[$product][$key]['count'] += $array['count'];
      if (isset($array['flavor'])) $data1[$product][$key]['flavor'] = $array['flavor'];
      if (isset($array['ip'])) $data1[$product][$key]['ip'] = $array['ip'];
    }
  }
}

function summarize($data)
{
  static $products = [];

  $summary = [];

  $summary['-'] = [
    'total' => $data['total'],
    'total_invalid' => $data['total_invalid'],
    'bytes' => $data['bytes'],
    'unique' => 0,
  ];

  foreach ($data['total_product'] as $product => $total) {
    if (!product_filter($product)) continue;
    $summary_product = [
      'total' => $total,
    ];
    if (isset($data['unique_product'][$product])) {
      $unique_product = $data['unique_product'][$product];
      $summary_product += [ 'unique' => count($unique_product) ];
      // A UUID should be unique to a product, as such this should provide an
      // accurate count of total unique across all products.
      $summary['-']['unique'] += $summary_product['unique'];
      $first_key = reset($data['unique_product'][$product]);
      if (isset($first_key['flavor'])) {
        $unique_flavors = array_column($data['unique_product'][$product], 'flavor');
        $flavors = array_unique($unique_flavors);
        $summary_product['flavors'] = [];
        foreach ($flavors as $flavor) {
          $summary_product['flavors'][$flavor] = count(array_keys($unique_flavors, $flavor));
        }
      }
    } else {
      $summary_product += [ 'unique' => 0 ];
    }
    $summary[$product] = $summary_product;

    // Keep track of which products have been included in previous summary.
    if (!isset($products[$product])) $products[$product] = true;
  }

  // Fill empty data with zeros to achieve appropriate result in graph.
  $missing = array_diff(array_keys($products), array_keys($summary));
  foreach ($missing as $product) {
    $summary[$product] = [
      'total' => 0,
      'unique' => 0,
    ];
  }

  return $summary;
}

function summarize_product_plus_key($data)
{
  static $keys = [];

  $summary = [];
  $products = array_merge(array_keys($keys), array_keys($data));
  foreach ($products as $product) {
    if (!product_filter($product)) continue;

    $keys_keys = isset($keys[$product]) ? array_keys($keys[$product]) : [];
    $data_keys = isset($data[$product]) ? array_keys($data[$product]) : [];
    $product_keys = array_merge($keys_keys, $data_keys);

    if (!isset($keys[$product])) $keys[$product] = [];
    $summary[$product] = [];
    foreach ($product_keys as $key) {
      // Fill empty data with zeros to achieve appropriate result in graph.
      $keys[$product][$key] = true;
      $summary[$product][$key] = isset($data[$product][$key]) ? $data[$product][$key] : 0;
    }
  }

  return $summary;
}

function product_filter($product)
{
  return (bool) preg_match(PRODUCT_PATTERN, $product);
}

function date_period_reversed($begin, $interval, $end)
{
  $interval = DateInterval::createFromDateString($interval);
  $period = new DatePeriod($begin, $interval, $end);
  return array_reverse(iterator_to_array($period));
}

function write_summary($interval, DateTime $value, $summary, $tags = [], $prefix = 'access')
{
  $measurement = $prefix . '_' . $interval;
  $points = [];
  foreach ($summary as $product => $fields) {
    $points[] = new Point($measurement, ['product' => $product] + $tags, $fields, $value->getTimestamp());
  }
  write($points);
  return count($points);
}

function write_flavors($interval, DateTime $value, $flavors)
{
  $measurement = 'access_' . $interval;
  $points = [];
  foreach ($flavors as $product => $unique_flavors) {
    foreach($unique_flavors as $flavor => $unique_count) {
      $tags = ['product' => $product, 'flavor' => $flavor];
      $fields = ['value' => $unique_count];
      $points[] = new Point($measurement, $tags, $fields, $value->getTimestamp());
    }
  }
  write($points);
  return count($points);
}

function write_summary_product_plus_key($interval, DateTime $date, $summary, $prefix)
{
  $measurement = $prefix . '_' . $interval;
  $points = [];
  foreach ($summary as $product => $pairs) {
    foreach ($pairs as $key => $value) {
      $points[] = new Point($measurement,
        ['product' => $product, 'key' => $key], ['value' => $value], $date->getTimestamp());
    }
  }
  write($points);
  return count($points);
}

function write($points)
{
  static $client;
  static $writeApi;

  if (!$client) {
    $client = new Client([
      "url" => "http://localhost:8086",
      "token" => "",
      "bucket" => "osrt_access/autogen",
      "org" => "-",
      "precision" => InfluxDB2\Model\WritePrecision::S
    ]);
    $writeApi = $client->createWriteApi();
  }

  if (!is_null($writeApi->write($points)))
    die('failed to write points');
}
