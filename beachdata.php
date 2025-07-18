<?php
// Get the URL from the query string
$baseURL = isset($_GET['u']) ? $_GET['u'] : 'https://datavisualization.dph.mass.gov/views/BeachesDashboard-CloudVersion-2025/ClosureTable.csv?refresh=y';
$beachName = isset($_GET['b']) ? $_GET['b'] : 'Shannon Beach @ Upper Mystic (DCR)';

// Check if this is a CSO API request
$isCSORequest = strpos($baseURL, 'eeaonline.eea.state.ma.us/dep/CSOAPI') !== false;

if ($isCSORequest) {
    // For CSO API, use the URL as-is (it already has all required parameters)
    $url = $baseURL;
    $referer = 'https://eeaonline.eea.state.ma.us/portal/dep/cso-data-portal/';
} else {
    // For beach data CSV, append the beach name parameter
    $url = $baseURL . '&Name=' . urlencode($beachName);
    $referer = 'https://datavisualization.dph.mass.gov';
}

// Initialize a cURL session
$ch = curl_init();

// Set the cURL options
curl_setopt($ch, CURLOPT_URL, $url);
curl_setopt($ch, CURLOPT_VERBOSE, true);
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_FOLLOWLOCATION, true);
curl_setopt($ch, CURLOPT_HTTPHEADER, array(
	'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
	'Referer: ' . $referer
));

// Execute the cURL request
$response = curl_exec($ch);

// Check for cURL errors
if ($response === false) {
	echo 'Curl error: ' . curl_error($ch);
} else {
	// Set the appropriate headers to allow CORS
	$origin = isset($_SERVER['HTTP_ORIGIN']) ? $_SERVER['HTTP_ORIGIN'] : '*';
	if (in_array($origin, ['https://jalkut.com', 'http://localhost:8080', 'http://localhost:8000'])) {
		header("Access-Control-Allow-Origin: $origin");
	} else {
		header("Access-Control-Allow-Origin: *");
	}
	header("Access-Control-Allow-Methods: GET, POST, OPTIONS");
	header("Access-Control-Allow-Headers: Origin, Content-Type, Accept, Authorization");
	
	// Set appropriate content type based on request type
	$contentType = $isCSORequest ? 'application/json' : 'text/plain';
	header("Content-Type: $contentType");

	// Output the response
	echo $response;
}

// Close the cURL session
curl_close($ch);
