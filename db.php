<?php
require_once 'config.php';

$cfg = require 'config.php';
$db = $cfg['db'];

try {
    $options = [
        PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ];
    $pdo = new PDO(
        "$db[driver]:host=$db[host];dbname=$db[dbname]",
        $db['user'],
        $db['pass'],
        $options
    );
    if ($db['driver'] === 'pgsql') {
        $pdo->exec("SET NAMES 'UTF8'; SET client_encoding = 'UTF8';");
    }
} catch (PDOException $e) {
    http_response_code(500);
    header('Content-Type: application/json; charset=UTF-8');
    echo json_encode(array("status" => "error", "message" => "Erreur de connexion à la base de données."));
    exit();
}
