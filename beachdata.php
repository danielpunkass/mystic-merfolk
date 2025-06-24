<?php
// Get the URL from the query string
$baseURL = isset($_GET['u']) ? $_GET['u'] : 'https://datavisualization.dph.mass.gov/views/BeachesDashboard-CloudVersion-2025/Results.csv?refresh=y';
$beachName = isset($_GET['b']) ? $_GET['b'] : 'Shannon Beach @ Upper Mystic (DCR)';
$url = $baseURL . '&Name=' . urlencode($beachName);

// Initialize a cURL session
$ch = curl_init();

// Set the cURL options
curl_setopt($ch, CURLOPT_URL, $url);
curl_setopt($ch, CURLOPT_VERBOSE, true);
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_FOLLOWLOCATION, true);
curl_setopt($ch, CURLOPT_HTTPHEADER, array(
	'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
	'Referer: https://datavisualization.dph.mass.gov'
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
	header("Content-Type: text/plain");

	// Output the response
	echo $response;
}

// Close the cURL session
curl_close($ch);
