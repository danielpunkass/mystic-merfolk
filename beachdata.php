<?php

// ---------- archive helpers ----------

function sanitizeBeachKey($name) {
    $key = preg_replace('/[^a-zA-Z0-9]+/', '_', $name);
    return trim($key, '_');
}

function archivePath($beachName, $year) {
    $dir = __DIR__ . '/archive/' . sanitizeBeachKey($beachName);
    if (!is_dir($dir)) {
        @mkdir($dir, 0755, true);
    }
    return $dir . '/' . $year . '.csv';
}

function rowTimestamp($line) {
    if (preg_match('#^"?(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})\s*(AM|PM)?#i', $line, $m)) {
        $hour = (int)$m[4];
        if (!empty($m[7])) {
            $ap = strtoupper($m[7]);
            if ($ap === 'PM' && $hour < 12) $hour += 12;
            if ($ap === 'AM' && $hour === 12) $hour = 0;
        }
        return mktime($hour, (int)$m[5], (int)$m[6], (int)$m[1], (int)$m[2], (int)$m[3]);
    }
    return 0;
}

function archiveResultsCsv($csv, $beachName) {
    $lines = preg_split('/\r\n|\r|\n/', trim($csv));
    if (count($lines) < 2) return;

    $header = $lines[0];
    $rowsByYear = [];
    foreach (array_slice($lines, 1) as $line) {
        if (trim($line) === '') continue;
        if (preg_match('#^"?\d{1,2}/\d{1,2}/(\d{4})#', $line, $m)) {
            $rowsByYear[$m[1]][] = $line;
        }
    }

    foreach ($rowsByYear as $year => $newRows) {
        $path = archivePath($beachName, $year);
        $merged = [];
        if (file_exists($path)) {
            $existing = preg_split('/\r\n|\r|\n/', trim(file_get_contents($path)));
            array_shift($existing); // drop header
            foreach ($existing as $line) {
                if (trim($line) !== '') $merged[$line] = true;
            }
        }
        foreach ($newRows as $line) {
            $merged[$line] = true;
        }

        $lines = array_keys($merged);
        usort($lines, function($a, $b) {
            return rowTimestamp($b) - rowTimestamp($a);
        });

        $content = $header . "\n" . implode("\n", $lines) . "\n";
        @file_put_contents($path, $content, LOCK_EX);
    }
}

// ---------- CORS ----------

function emitCorsHeaders() {
    $origin = isset($_SERVER['HTTP_ORIGIN']) ? $_SERVER['HTTP_ORIGIN'] : '*';
    if (in_array($origin, ['https://jalkut.com', 'http://localhost:8080', 'http://localhost:8000'])) {
        header("Access-Control-Allow-Origin: $origin");
    } else {
        header("Access-Control-Allow-Origin: *");
    }
    header("Access-Control-Allow-Methods: GET, POST, OPTIONS");
    header("Access-Control-Allow-Headers: Origin, Content-Type, Accept, Authorization");
}

// ---------- main ----------

$beachName = isset($_GET['b']) ? $_GET['b'] : 'Shannon Beach @ Upper Mystic (DCR)';
$archiveYear = isset($_GET['archive']) ? preg_replace('/[^0-9]/', '', $_GET['archive']) : '';

// Default live URL (Results.csv). Used in proxy mode, and also opportunistically in archive mode
// to keep archives fresh as long as upstream still serves rows for the requested year.
$liveResultsURL = 'https://datavisualization.dph.mass.gov/views/BeachesDashboard-CloudVersion-2025/Results.csv?refresh=y';

$baseURL = isset($_GET['u']) ? $_GET['u'] : $liveResultsURL;

$isCSORequest = strpos($baseURL, 'eeaonline.eea.state.ma.us/dep/CSOAPI') !== false;

if ($isCSORequest) {
    $url = $baseURL;
    $referer = 'https://eeaonline.eea.state.ma.us/portal/dep/cso-data-portal/';
} else {
    $url = $baseURL . '&Name=' . urlencode($beachName);
    $referer = 'https://datavisualization.dph.mass.gov';
}

$isResultsRequest = !$isCSORequest && strpos($url, 'Results.csv') !== false;

// Fetch live data via cURL.
$ch = curl_init();
curl_setopt($ch, CURLOPT_URL, $url);
curl_setopt($ch, CURLOPT_VERBOSE, true);
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_FOLLOWLOCATION, true);
curl_setopt($ch, CURLOPT_HTTPHEADER, array(
    'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Referer: ' . $referer
));
$response = curl_exec($ch);

// Archive any successfully-fetched Results.csv data (visit-driven archival).
if ($isResultsRequest && $response !== false) {
    try {
        archiveResultsCsv($response, $beachName);
    } catch (Throwable $e) {
        error_log("Archive failed: " . $e->getMessage());
    }
}

emitCorsHeaders();

// Archive read mode: return the requested year's archive file, ignoring the live response.
if ($archiveYear !== '') {
    header("Content-Type: text/plain");
    $path = archivePath($beachName, $archiveYear);
    if (file_exists($path)) {
        readfile($path);
    } else {
        // Empty archive: return just a CSV header so the client parses cleanly.
        echo "Date and Time,Indicator,Threshold,Date and Time,Results\n";
    }
    exit;
}

// Proxy mode: return the live response.
if ($response === false) {
    echo 'Curl error';
} else {
    header("Content-Type: " . ($isCSORequest ? 'application/json' : 'text/plain'));
    echo $response;
}
