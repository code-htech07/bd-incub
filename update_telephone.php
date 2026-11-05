<?php
header("Access-Control-Allow-Origin: *");
header("Content-Type: application/json; charset=UTF-8");
header("Access-Control-Allow-Methods: POST, OPTIONS");
header("Access-Control-Allow-Headers: Content-Type, Authorization, X-Requested-With");

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(200);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(array("status" => "error", "message" => "Méthode non autorisée."));
    exit;
}

require_once 'admin_auth.php';
requireAdmin();
require_once 'db.php';

$data = json_decode(file_get_contents("php://input"));

if (is_object($data) && !empty($data->id) && isset($data->telephone)) {
    $query = "UPDATE etudiant SET telephone = :telephone WHERE id = :id";
    $stmt = $pdo->prepare($query);
    $stmt->bindParam(':telephone', $data->telephone);
    $stmt->bindParam(':id', $data->id);

    if ($stmt->execute()) {
        echo json_encode(array("status" => "success", "message" => "Téléphone mis à jour avec succès !"));
    } else {
        http_response_code(500);
        echo json_encode(array("status" => "error", "message" => "Mise à jour échouée."));
    }
} else {
    http_response_code(400);
    echo json_encode(array("status" => "error", "message" => "Données incomplètes."));
}