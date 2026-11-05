<?php
header('Access-Control-Allow-Origin: *');
header('Content-Type: application/json; charset=UTF-8');
header('Cache-Control: no-store');
header('Access-Control-Allow-Methods: POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type, Authorization, X-Requested-With');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(200);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(array('status' => 'error', 'message' => 'Méthode non autorisée.'));
    exit;
}

require_once 'db.php';

$data = json_decode(file_get_contents('php://input'), true);
$matricule = isset($data['matricule']) && is_string($data['matricule']) ? strtoupper(trim($data['matricule'])) : '';

if ($matricule === '' || strlen($matricule) > 40) {
    http_response_code(400);
    echo json_encode(array('status' => 'error', 'message' => 'Matricule requis.'));
    exit;
}

try {
    $query = "SELECT e.matricule, e.nom, e.prenoms, e.date_naissance,
                     e.telephone, e.email, e.sexe, e.allergies_affection,
                     f.libelle AS filiere, n.libelle AS niveau
              FROM etudiant e
              LEFT JOIN filiere f ON e.filiere_id = f.id
              LEFT JOIN niveau n ON e.niveau_id = n.id
              WHERE e.matricule = :matricule";
    $stmt = $pdo->prepare($query);
    $stmt->execute(array(':matricule' => $matricule));
    $row = $stmt->fetch();

    if (!$row) {
        http_response_code(404);
        echo json_encode(array(
            'status' => 'error',
            'message' => 'Matricule et date de naissance non reconnus dans la base UPB.'
        ), JSON_UNESCAPED_UNICODE);
        exit;
    }

    http_response_code(200);
    echo json_encode(array(
        'status' => 'success',
        'message' => 'Vos informations ont été retrouvées.',
        'data' => $row
    ), JSON_UNESCAPED_UNICODE);
} catch (PDOException $exception) {
    error_log('Vérification étudiant UPB : ' . $exception->getMessage());
    http_response_code(500);
    echo json_encode(array('status' => 'error', 'message' => 'Erreur lors de la vérification des informations.'));
}
